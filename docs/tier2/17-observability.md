# 17 — Observability

*Tier 2: authored from `gateway/src/metrics.rs`, `telemetry.rs`, `main.rs`,
`handlers.rs`, and `vrp/observe.py`.*

Three surfaces: Prometheus metrics, structured logs, and optional OTLP traces.
All three are configured once at startup and none of them can fail the process.

## Metrics

Scraped from `METRICS_ENDPOINT` (default `/metrics`), which is never rate
limited so a scrape is never shed.

| Series | Type | Labels |
|---|---|---|
| `http_requests_total` | counter | `handler`, `method`, `status` |
| `http_request_duration_seconds` | histogram | `handler`, `method` |
| `http_request_duration_highr_seconds` | histogram | none |
| `http_request_size_bytes` | summary | `handler` |
| `http_response_size_bytes` | summary | `handler` |
| `cache_lookups_total` | counter | `tier`, `result`, `service` |
| `process_*` | gauges/counters | none, **Linux only** |

Almost every decision here is about matching `prometheus-fastapi-
instrumentator`, so that dashboards and alerts survived the port:

- **Bucket sets are copied, not chosen.** `LATENCY_BUCKETS` is `[0.1, 0.5,
  1.0]`; the high-resolution companion carries all 21 of the instrumentator's
  boundaries out to 60 s. The prometheus crate's own defaults would have given
  the same metric name a different bucket set and made `histogram_quantile`
  disagree over identical traffic. Truncating the high-resolution set at 7.5
  had exactly that effect once, putting every slow request in `+Inf`.
- **Status is grouped**, not exact: `2xx`, `4xx`, `5xx`. Emitting the full code
  under the same label left every existing `status="5xx"` query matching
  nothing.
- **Sizes are summaries with only `_sum` and `_count`.** The prometheus crate
  has no summary type, and substituting a histogram would have added a
  `histogram` TYPE line and a spray of `_bucket` series Python never emitted —
  so `SizeSummary` implements the collector directly.
- **`process_*` collectors need `/proc`.** The Docker deployment and CI get
  them; the FreeBSD jail reports no `process_*` series at all. A dashboard
  reading `process_resident_memory_bytes` will be blank there, by design.

### Label cardinality is bounded on purpose

`handler_label` maps a request path to a closed set. Paths carrying
coordinates — `/tile/...` — collapse to their route pattern
`/tile/{profile}/{z}/{x}/{y}.mvt`, and anything unrouted collapses to `none`
(the instrumentator's spelling; `other` would have matched no existing query).
`service_label` does the same for upstream endpoints, keeping only `route`,
`table`, `match`, `trip` and `nearest` and folding the rest into `other`,
"because the raw endpoint carries the request's coordinates".

**`/v1/route` and `/route` get different handler labels.** The rate limiter
folds them into one bucket; metrics deliberately do not, because the entire
point of the deprecation window is watching unversioned traffic fall to zero,
and a dashboard that could not tell them apart could not say when it was safe
to remove the alias.

### Cache metrics have a trap in them

`cache_lookups_total{tier="l1"|"l2", result="hit"|"miss"}` records L2 only
after L1 misses. The tiers are **not independent**: L2's series are a subset of
L1's misses, and summing them for a hit rate double-counts. An unconfigured
Redis emits no L2 series at all.

## Logs

`tracing-subscriber` with a custom formatter that reproduces
`logging_config.py`'s line shape: `YYYY-MM-DD HH:MM:SS,mmm [LEVEL] target:
message`. Two deliberate divergences:

- **Timestamps are UTC**, where Python's `asctime` was local. Resolving a local
  zone needs a tz database the binary does not carry, and an unambiguous log
  across the jail and the container was judged worth more than an exact
  character match with an implementation that no longer runs. The civil-date
  conversion is written out by hand for the same reason.
- **ANSI colour is off unconditionally.** Both deployments send this to a file,
  and the default filled the jail's log with escape sequences.

`DEBUG=true` selects debug level and is **authoritative for this crate**:
`RUST_LOG` is still honoured for dependency noise, but a stray `RUST_LOG=warn`
used to silently override `DEBUG` and produce warn-level application logs.
`APPEND_TO_STDERR` chooses the stream.

## Tracing

Off unless `OTLP_ENDPOINT` is set. Transport is OTLP over HTTP rather than gRPC
— reqwest is already a dependency, and the gRPC stack would add tonic and prost
to a build that has to fit a 2 GB jail.

Both halves of propagation are wired, and the module records why that matters:

- **Outbound** (`inject_context`): writes `traceparent` onto the upstream
  request so `osrm-routed` joins the caller's trace.
- **Inbound** (`adopt_incoming_context`): parents the server span on the
  caller's context. Injecting downstream without extracting here "left the
  gateway starting a fresh trace on every request, so a caller's trace ended at
  this hop — and only fixing the outbound half looks like it works right up
  until someone traces end to end".

The span is `http.server`, carrying `http.request.method`, `http.route` and
`http.response.status_code`. The status field is **declared `Empty` and
recorded after the response**, because `record` on an undeclared field is
silently dropped. Before this span existed, an OTLP endpoint could be correctly
configured and receive an empty trace: the layer was installed over no spans at
all.

**Shutdown flushes under a 2-second budget.** `provider.shutdown()` drains the
batch processor, and against an unreachable collector that drain does not
finish — measured at over a minute before being killed. Since it runs on the
way out of `main`, an unbounded wait would hang SIGTERM: Docker would wait its
full stop timeout and then SIGKILL. Losing the last batch is the better trade.

An exporter that cannot be built is logged and dropped. A gateway that refuses
to start because its collector is down is worse than one that starts without
traces.

## Health and readiness

| Endpoint | Behaviour |
|---|---|
| `/health` | always 200, body reports `healthy`/`degraded` and `osrm_backend` up/down |
| `/ready` | 503 when the engine is down, so a balancer drains this node |

Both probe the driving engine with a real `/route` call against
`HEALTH_CHECK_COORDS`, bypassing cache and retry, under
`HEALTH_CHECK_TIMEOUT`. Neither is versioned: they are an operator's contract
with its orchestrator, not a client integration.

## On the platform side

`vrp/observe.py` is a different kind of observability: a **run record** for a
solve (NFR-06, CON-4), not a time series. It is what makes a plan explainable
after the fact, and pairs with the snapshot machinery in doc 29.
