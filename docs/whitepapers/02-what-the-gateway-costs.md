# What the Gateway Costs

**An intermediate technical report.** For engineers operating the gateway,
integrating against it at volume, or changing it. Assumes
[Paper 01](01-routing-a-delivery-day.md); assumes no Rust.

Measurements are from `10.211.55.33:8000` against the Costa Rica corpus, by
scripts in [`experiments/`](experiments). The design reference is
[`docs/SDD.md`](../SDD.md); this report measures what that document specifies,
and corrects it in one place.

---

## 1. The thesis

7,560 lines of Rust across 20 modules (measured 2026-09-02), and almost none
of it is routing. The
engine routes. The gateway holds a **contract** stable — fixed request and error
shapes, fixed cache semantics, fixed metric names, fixed limits — in front of an
engine that has none of those and cannot be given them.

The useful question to ask of any decision here is *what would break for a
client if this were done the obvious way?*

---

## 2. Experiment: what it costs to serve

`experiments/e04_scaling.py`, one depot, `capacity: 35`, stops drawn from the
GAM. Wall-clock is end to end from the client, so it includes the gateway's own
matrix fetch, its `/trip` calls, and the network.

**`/vrp`** (`results/e04_scaling.json`)

| Stops | Vehicles | Wall time | Per stop | Plan distance |
|---:|---:|---:|---:|---:|
| 50 | 2 | 85.8 ms | 1.72 ms | 315 km |
| 100 | 3 | 80.8 ms | 0.81 ms | 531 km |
| 250 | 8 | 187.7 ms | 0.75 ms | 1,019 km |
| 500 | 15 | 401.3 ms | 0.80 ms | 1,790 km |
| 1,000 | 29 | 601.5 ms | 0.60 ms | 3,311 km |
| 2,000 | 58 | 1,181.7 ms | 0.59 ms | 6,224 km |

Two things to take from this. **Scaling is essentially linear** to 2,000 stops —
no knee, no cliff. And **per-stop cost falls by 3×** across the range, because
the fixed costs (one matrix, request handling) amortise while the variable work
is per-chunk `/trip` calls that fan out concurrently.

A 2,000-stop national plan in 1.2 seconds is genuinely fast, and worth saying
plainly before [§7](#7-what-the-speed-buys-and-what-it-does-not) takes some of it
back.

**`/matrix`**

| Side | Cells | Wall time |
|---:|---:|---:|
| 10 | 100 | 7.9 ms |
| 25 | 625 | 15.7 ms |
| 50 | 2,500 | 36.4 ms |
| 75 | 5,625 | 55.2 ms |
| 100 | 10,000 | 78.9 ms |

Roughly linear in cells, and 100×100 is the ceiling: `MATRIX_MAX_CELLS` defaults
to 10,000. That bound is not arbitrary — it mirrors what `osrm-routed` itself
enforces, which refuses a table request when `sources × destinations` exceeds
`--max-table-size` squared. The gateway's ceiling is the square of the value you
passed the engine.

---

## 3. Experiment: what a cache hit is worth

Same script. One payload no earlier call had used, issued three times in a row:

| Call | Latency |
|---|---|
| Cold | **25.7 ms** |
| Warm | **2.1 ms** |
| Warm again | **2.2 ms** |

**11.5×.** That is a hit-versus-miss figure on one process, not a hit rate over a
workload — it says what a hit saves, not how often you get one. But it sets the
value of the two cache tiers precisely, and it explains why so much care goes
into the key.

The key is the endpoint path plus a SHA-256 of the sorted parameters, and the
serialisation **byte-reproduces Python's `json.dumps(..., sort_keys=True)`** —
which is why `pyfloat.rs` exists, reimplementing Python's float repr. That looks
like a strange amount of work for a cache key. It bought two things: the Rust and
Python gateways could share one Redis during the transition, and it pins a format
that would otherwise drift. A drifting cache-key format does not fail; it quietly
stops hitting, and you meet it later as an unexplained 11.5× latency regression.

Redis errors are always swallowed and its timeouts bounded hard. A cache that can
take the system down is worse than no cache.

---

## 4. Experiment: the hysteresis band, and a correction

**The SDD describes `hysteresis_m` as "a hysteresis band that keeps territories
stable between runs". The code does something else**, and the difference matters
to anyone tuning it.

There is no previous assignment in a `/vrp` request — the field does not exist —
so nothing is compared between runs. `select_depot` in
`gateway/src/vrp/allocate.rs` anchors each stop to its **Euclidean**-nearest
depot, and leaves that anchor only when another depot's road cost beats it by
more than the band:

```rust
let anchor = argmin(euclidean_m);
// ... sanity and unreachable checks ...
if best_val < anchor_val - hysteresis { return best; }
anchor
```

So the band is a standing preference for stable *geometry* over the matrix's
current opinion. Stability between runs is the consequence — geometry does not
change when the matrix does — but the mechanism is within a single run, and that
is what determines when it bites.

Whether it bites at all depends on **depot spacing**, which no document
mentions. `experiments/e05_hysteresis.py` sweeps the band over 400 stops in two
configurations (`results/e05_hysteresis.json`):

**Six national depots, tens of km apart**

| Band | Stops held at anchor | Extra road distance |
|---:|---:|---:|
| 0 m | 0 / 400 | — |
| 1,000 m | 0 / 400 | 0 km |
| **2,000 m (default)** | **2 / 400 (0.5%)** | **2.6 km** |
| 8,000 m | 11 / 400 | 58.2 km |
| 16,000 m | 27 / 400 | 238.4 km |
| `radial` (no matrix) | 36 / 400 | **996.4 km** |

**Four GAM depots, 8–25 km apart**

| Band | Stops held at anchor | Extra road distance |
|---:|---:|---:|
| 250 m | 3 / 400 | 0.3 km |
| 1,000 m | 4 / 400 | 0.6 km |
| **2,000 m (default)** | **17 / 400 (4.3%)** | **20.8 km** |
| 4,000 m | 29 / 400 | 62.5 km |
| 8,000 m and above | 30 / 400 | 66.5 km |
| `radial` (no matrix) | 30 / 400 | 66.5 km |

Three findings:

1. **At the shipped default, on the shipped depots, the band is nearly inert** —
   two stops in four hundred. Anyone tuning it on this corpus is tuning
   something that is barely doing anything.
2. **At urban spacing the same default holds eight times as many stops**, and
   costs 20.8 km. The knob's effect is a function of your depot geometry, not of
   the number you set.
3. **Above 4 km the urban configuration saturates** — identical to `radial`, which
   never consults the matrix at all. Past that point the band has not been
   loosened, it has been switched off.

The `radial` rows also price the matrix itself. Ignoring road costs entirely
costs **996 km** across 400 nationally-distributed stops and only **67 km** in
the GAM — because with far-apart depots, getting the assignment wrong is
expensive, and in a dense metro the alternatives are all nearby.

---

## 5. The pipeline, and why order is load-bearing

```
observe ──► rate limit ──► require JSON ──► catch panic ──► handler
(metrics,      (429)        (422 on bad      (500 rather
 span)                       media type)      than a dropped
                                              connection)
```

**`observe` outermost** so a shed 429 is still counted. Put the limiter outside
it and metrics go quiet exactly when the gateway is under pressure — the graph
flattens when it should spike, and it reads as calm.

**`catch_panic` innermost** so a panic unwinds back out through `observe` and is
recorded as the 500 it became.

If you add middleware, place it by asking which failures it must still see.

---

## 6. Validation is the contract

Three properties that are less obvious than they look.

**The 422 body reproduces pydantic's error shape** — `type`, `loc`, `msg`,
`input`, `ctx`. This gateway replaced a FastAPI service and clients branch on
`type` and `loc`. Changing them breaks integrations with no signal: nothing
fails to compile, nothing 500s, error handling just silently stops matching.

**Decoding collects every failure, not the first.** serde stops at the first
problem, so each failure is patched with an acceptable value and the document
re-read until all are found. A body wrong in three places reports three errors.
The patched copy is a throwaway — `input` is always filled from what was sent.

**Coercion follows pydantic's lax mode.** `"-84.09"` is a valid float, `35.0` a
valid integer, `35.5` is not. Not laxity for its own sake: it is the contract
clients already depend on.

The generalisation: *an error format is API surface*, versioned and tested like
a response body.

---

## 7. What the speed buys, and what it does not

§2 shows `/vrp` is fast. It is worth being precise about what that speed is
spent on, because §10 of [Paper 03](03-feasibility-is-a-gate.md) measures what it
does not buy.

`/vrp` runs two phases. **Allocate**: one depot-to-stop matrix, then per stop a
Euclidean anchor, an `argmin` over road cost, the hysteresis band of §4, and a
sanity override refusing an implausible matrix answer. **Sequence**: sort each
depot's stops by sweep angle, cut into chunks of `min(VRP_CHUNK_SIZE, capacity)`
bounded by the `/trip` coordinate cap, and send each chunk to OSRM `/trip`.
Chunks fan out concurrently and the first failure cancels its siblings.

Delegating ordering to `/trip` is deliberate (D-5): the engine already solves the
TSP well, and writing another is the expensive way to do worse.

But **sweep-and-cut fixes each vehicle's load before anything is optimised.**
The partition is chosen by compass angle, and no later step can revisit it.
That is where the quality goes, and Paper 03 measures the size of it.

---

## 8. Relay, do not re-encode

The gateway passes the engine's response bytes back unaltered. The performance
argument is real but secondary; the correctness argument is the reason. An
earlier version that re-encoded shifted the last unit in the last place on some
coordinates — every number *approximately* right, which survives review, passes
tolerance-based tests, and surfaces months later as an unreproducible complaint.

The consequence to remember: **the gateway cannot enrich a relayed response.**
If you want a field added to `/route` output, you are proposing to give up D-1,
and that trade needs stating out loud.

---

## 9. Rate limiting

Fixed-window, per client IP, per endpoint bucket. Counted in Redis when
`REDIS_URL` is set so a fleet shares one allowance, falling back to an in-process
map otherwise.

Know the fallback's semantics before an incident: with Redis down, *N* instances
each enforce the full limit independently, so the effective global limit is *N*
times what you configured. Degrading to loose limiting beats degrading to no
service — but it is a fact to know, not to discover.

One asymmetry: **an unparseable rate limit stops the process at startup**, while
every other malformed setting falls back to its default. The failure mode is not
"wrong value" but "no limit at all", and an endpoint that comes up silently
unlimited is the single failure a limiter must not have.

---

## 10. Admission control

A solve is expensive in memory in a way a relay is not, and unbounded
concurrency on `/vrp` is how a 2 GB jail dies. `VRP_MAX_CONCURRENCY` bounds
concurrent solves, `VRP_QUEUE_TIMEOUT` bounds the wait, and an optional
queue-depth bound rejects immediately when waiting is hopeless. Shed requests get
503 with `Retry-After`.

The shape is worth copying: **bound the resource, give the client a number, fail
fast when waiting cannot help.** A request that queues 30 seconds and then fails
has spent the client's patience and your memory to no purpose.

---

## 11. The ceilings

| Ceiling | Value | Behaviour | Source |
|---|---|---|---|
| Matrix cells | 10,000 (`MATRIX_MAX_CELLS`) | 422 naming the limit | Mirrors the engine's `--max-table-size²` |
| Upstream URL | ~24,750 bytes | 422 naming the limit | **Measured.** Past it the engine drops the connection and retries turn it into an uninformative 500 |
| `/match` points | ~720 | Hits the URL ceiling | Coordinates travel in the URL — OSRM's contract |
| VRP concurrency | `VRP_MAX_CONCURRENCY` | 503 + `Retry-After` | §10 |

The URL figure is the one to internalise as a working practice. A plausible
guess would have been 8 KB — a common HTTP convention — and it would have broken
2,000-stop solves that demonstrably work. The number in the code is the one
somebody measured against this engine.

---

## 12. Configuration

Settings are declared in one place: the `settings!` table in
`gateway/src/config.rs`, carrying each name, type and committed default
together. That table is the declaration of record. Values live in
`deploy/env/app.env`, loaded by both deployments.

> **Both documents that quote a count are out of step with it.**
> `docs/configuration.md` says "all 29 settings"; `SDD.md` §1.4 says 35; the
> table declared 36 when this was measured (2026-09-02). A count in prose goes
> stale the first time somebody adds a setting, which is exactly what happened
> here — so treat `config.rs` as the answer and the prose as a hint.

Three tiers, highest priority last: `app.env`, then deployment overrides, then
the real process environment. Tier 2 beats tier 1 on both paths for the same
underlying reason — Compose documents `environment:` as overriding `env_file:`,
and dotenv takes the **last** occurrence of a duplicated key, which is why the
jail installer appends its overlay rather than prepending it.

Only `OSRM_BASE_URL` and `REDIS_URL` are overridden per deployment, so editing
those two in `app.env` has no effect at all — a trap worth knowing before you
spend an afternoon on it.

Conformance tests check every `app.env` key resolves to a declared setting and
every default matches, with a commented allowlist for three inert keys and the
four process-level settings (`HOST`, `PORT`, `WORKERS`, `FORWARDED_ALLOW_IPS`)
each deployment sets per instance. That is the right pattern: not "the two
agree", but "the two agree except here, and here is why".

---

## 13. Observability and deployment

Prometheus metrics keep the names, types, labels and bucket boundaries the
previous Python implementation exposed, so dashboards and alerts survived the
rewrite. A rewrite that silently renames your metrics has moved its cost onto
whoever is on call. Tracing is a server span per request and a client span per
upstream call, with W3C trace context extracted inbound and injected outbound.
`process_*` metrics come from `/proc` and are Linux-only.

Two deployments, no others: Docker (`make compose-up`) and a FreeBSD jail
(`make jail-up`). They share no state and can run side by side, and **nothing in
the gateway differs between them** — `OSRM_BASE_URL` is the only change, and it
is already a setting. Defend that property in review: the moment a deployment
needs a code path, you have two products.

---

## 14. Measured position

From [`SCALING_READINESS_PLAN.md`](../planning/SCALING_READINESS_PLAN.md),
measured on the jail:

| Attribute | Position |
|---|---|
| Latency | No meaningful difference from the Python implementation on ordinary traffic — the engine is the constraint. ~2× faster on cache hits, where the gateway is the whole request |
| Memory | ~57 MB under load against ~221 MB |
| Throughput | ~3× per-worker headroom, unspendable while the engine saturates first |
| Dependencies | 17 direct crates, no TLS stack |

The honest reading: the rewrite bought **memory and headroom, not latency**.
Latency is bounded by the engine and no work in the gateway moves it. (Note that
the "~2×" there is Rust-versus-Python on cache hits — a different measurement
from §3's 11.5× hit-versus-miss on one gateway.)

---

## 15. Changing it

- **`models.rs` is 1,222 lines deliberately.** Validation is a contract, and a
  contract belongs in one readable place.
- **A relayed endpoint** needs: a request type in `models.rs`, query construction
  in `osrm/params.rs`, a handler, a rate-limit bucket, a cache decision. The
  OpenAPI schema is generated from the serving types and follows automatically.
- **A computed endpoint** needs all of that plus an admission decision. If it can
  allocate unboundedly, it needs a gate.
- **Do not enrich a relayed response** without explicitly giving up D-1.
- **Do not add a code path that differs per deployment.**
- **State where your numbers came from.** Every limit here names its
  measurement; a new one that does not will be the first thing distrusted.

---

## 16. Reproducing this paper

```sh
export WHITEPAPER_GATEWAY=http://10.211.55.33:8000
cd docs/whitepapers/experiments
PYTHONPATH=../../.. uv run python e04_scaling.py     # §2, §3
PYTHONPATH=../../.. uv run python e05_hysteresis.py  # §4
```

Next: [03 — Feasibility Is a Gate](03-feasibility-is-a-gate.md), which measures
what this gateway's routing costs you against a real solver.
