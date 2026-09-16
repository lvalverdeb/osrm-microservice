# 15 — Request lifecycle and caching

*Tier 2: authored from `gateway/src/osrm/client.rs`, `cache.rs`,
`redis_cache.rs`, `main.rs`.*

## One request, end to end

1. **`catch_panic`** wraps everything. A panic becomes
   `500 {"detail":"Internal server error"}` with the cause logged, never sent.
2. **`observe`** labels the request (`handler_label`), starts two latency
   timers, opens an `http.server` span and adopts any incoming `traceparent`.
3. **`rate_limit`** looks up the endpoint's allowance; no bucket means no limit.
4. **`require_json_body`** refuses a POST whose `Content-Type` is not
   `application/json` or a `+json` suffix — including an absent one, which is
   what a bare `curl -d` sends.
5. **`announce_deprecation`** adds `Deprecation`, `Link` and optionally
   `Sunset` when the path is unversioned and has a `/v1` successor.
6. The **handler** validates the body, builds upstream query parameters, and
   calls `OsrmClient`.
7. **`OsrmClient::get`** builds a cache key, consults L1 then L2, and on a miss
   fetches upstream with bounded retries, writing through both tiers.
8. `observe` buffers the response body to measure it, records size, status and
   duration, and stamps the status onto the span.

Step 8 is why every response is fully buffered in memory. The comment explains
the alternative did not work: axum does not set `Content-Length` before
middleware runs — hyper adds it while encoding the wire — so reading the header
recorded zero for every request.

## Two cache tiers, consulted in order

| | L1 | L2 |
|---|---|---|
| Implementation | `moka` in-process | Redis |
| Sized by | `L1_CACHE_MAXSIZE` (1024) | `REDIS_MAXSIZE` |
| Expiry | `L1_CACHE_TTL` (900 s) | `REDIS_TTL` (900 s) |
| Shared between instances | no | yes |
| Missing means | — | the tier is skipped entirely |

`lookup_cached` consults L1, and only on a miss consults L2; an L2 hit is
**promoted into L1** so the next hit costs nothing. `store_cached` writes
through both.

**Do not sum the tiers for a hit rate.** The code says so twice, in
`metrics.rs` and again in `client.rs`: L2 is consulted only after L1 misses, so
its series are a subset of L1's misses and adding them double-counts. When no
Redis is configured the L2 series are not emitted at all, so an unconfigured
deployment reports nothing rather than an unbroken run of misses.

## The cache key is a compatibility artefact

`build_cache_key(endpoint, params)` produces
`"{endpoint}:{sha256(dump_params(params))}"` — the endpoint in plaintext, only
the parameters hashed. Two properties follow:

- **The endpoint carries the coordinates**, so keys are not safe to log
  wholesale. The module says this outright.
- **Key order does not matter but parameter typing does.** `dump_params` sorts
  keys through a `BTreeMap` while the query string preserves insertion order,
  so two requests that build the same parameters in a different order share an
  entry.

The serialisation reproduces `json.dumps(params, sort_keys=True, default=str)`
exactly, including three divergences from `serde_json`'s defaults: the `", "`
and `": "` separators, `ensure_ascii` escaping (with astral characters written
as surrogate pairs), and Python's float `repr`, which writes `1e-07` where Rust
writes `1e-7` and `50.0` where Rust writes `50` (`gateway/src/pyfloat.rs`). The
tests pin digests produced by the Python implementation; if they fail, the two
gateways have silently split their shared Redis into separate namespaces.

**One asymmetry survives.** The Rust tier stores the engine's response bytes
verbatim; the Python one stored `json.dumps` of the decoded body. Same JSON,
different whitespace. Keys match, so the two share entries, and a value written
by Python is relayed with Python's spacing. Cosmetic, and only reachable while
both gateways run against one Redis.

## Why bytes rather than parsed JSON

`OsrmClient::get` returns `Arc<Vec<u8>>` and the proxy endpoints relay it
untouched. A decode/re-encode cycle "shifted about 1 ULP of some geometry
coordinates, because Python's float repr and Rust's shortest-round-trip
formatter do not always choose the same f64 for the same decimal text".
Relaying bytes makes the body byte-identical to the engine's and skips the
parse on the hot path. Only `/matrix-graph` and the VRP endpoints decode, via
`get_json`, because they compute on the response.

## Retries, and the status they produce

`RetryPolicy` is `OSRM_RETRY_ATTEMPTS` (3) attempts with exponential backoff
clamped between `OSRM_RETRY_MIN` (1 s) and `OSRM_RETRY_MAX` (10 s). The clamp
is hand-written rather than `f64::clamp`, which panics when min > max: tenacity
computes `max(min, min(result, max))` and yields `min` for that
misconfiguration, and a bad settings pair must not take the request down.

- **4xx is final.** Passed straight through with the engine's own status.
- **5xx and transport failures are retried.**
- **Exhausted retries surface as 500**, not as the engine's status. This is
  reproduced from Python on purpose: tenacity raised `RetryError`, which is not
  an `HTTPStatusError`, so it fell to the generic handler. A persistently
  503-ing engine therefore reads as 500 here.

Redirects are refused outright (`Policy::none()`): httpx followed none and
reqwest follows ten by default, and "a redirect from the engine is a
misconfiguration, not a route to chase".

## Two things that bypass the cache

**Vector tiles** (`get_tile`): no cache, no retry, raw bytes — as in Python.
Note the axis reorder, `/tile/{profile}/{z}/{x}/{y}` inbound against OSRM's
`tile({x},{y},{z})`.

**The health probe** (`ping`): bypasses cache and retry so `/health` and
`/ready` answer within `HEALTH_CHECK_TIMEOUT` (2 s) rather than inheriting the
request-path budget. It always probes the driving engine, because readiness is
about the graph this gateway always has.

## Redis degrades, it does not fail

Every Redis error is logged and swallowed: losing Redis costs cache hits and
nothing else. The module notes this posture is load-bearing — the jail
deployment ran with Redis unreachable and served traffic throughout, which is
how the problem stayed hidden.

Two bounds keep an unavailable Redis cheap:

- `CONNECT_TIMEOUT` of 2 s on both connect and command.
- `set_number_of_retries(0)`, because `ConnectionManager`'s default of six
  retries with exponential backoff "turned one lookup against a dead Redis into
  minutes of waiting".

Availability is decided by **configuration, not reachability**: an empty
`REDIS_URL` disables the tier, but a configured-and-dead Redis costs a failed
round trip per lookup rather than being switched off. A failed connection
attempt leaves the once-cell empty so the next lookup retries, which is the
reconnect path.

`REDIS_TTL=0` stores nothing. Redis rejects `EX 0`, and rounding up to one
second would have made "do not cache" mean "cache for a moment".

## The URL length ceiling

OSRM takes coordinates in the path, so a long `/match` trace or a wide
`/matrix` builds a request line of tens of kilobytes. `OSRM_MAX_URL_BYTES`
(24,000) is checked **before the first attempt** — a URL that is too long is
too long every time, and retrying only multiplies the wait before the same
failure. Crossing it is a 422 naming both numbers, not a connection reset.

The figure was measured rather than assumed: `osrm-routed` 5.x answers up to
~24,750 bytes and drops the connection from ~25,000. Lower it if a proxy sits
in front of the engine — nginx and Apache both cap a request line near 8 KB.
