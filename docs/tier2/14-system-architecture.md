# 14 — System architecture

*Tier 2: authored from the code. Every claim below cites the file it came from;
the reference tables it links to are generated (`docs/tier1/`).*

## Two systems, one repository

| | Routing gateway | Routing platform |
|---|---|---|
| Language | Rust (axum) | Python 3.13 |
| Location | `gateway/` | `vrp/` |
| Size | ~7,950 lines across 20 files | ~18,060 lines across 64 modules |
| Serves | HTTP, in production | nothing; it is a library |
| Talks to | `osrm-routed` over plain HTTP | the gateway, over HTTP |

They are not layers of one service. The gateway is a caching, validating,
rate-limiting proxy in front of `osrm-routed` that also does a light,
self-contained form of vehicle routing. The platform in `vrp/` is a full
vehicle-routing system — time windows, hours of service, electric fleets,
dynamic dispatch — that uses the gateway only as a source of travel times.

**Nothing wires them together yet, and that is deliberate.** `vrp/api.py`
states the reason: how the Rust gateway should reach a Python solver is an open
architectural question the specification does not answer, and settling it
inside a task that does not need it would bury the decision. So `vrp/api.py`
ships `/verify` as a pure function from (problem, plan) to a report — the one
endpoint in the public surface that needs no solver — "ready for whichever
process ends up hosting it". Read that as: **the integration boundary is
unbuilt, not broken.**

## There are two different VRP implementations

This surprises everyone who greps for "vrp", so it is worth stating plainly.

**`POST /vrp` in the gateway** (`gateway/src/vrp/solve.rs`,
`gateway/src/vrp/allocate.rs`) assigns each stop to its cheapest depot in a
single pass with no convergence loop, splits the result into chunks, and
delegates each vehicle's ordering to `osrm-routed`'s own `/trip` service. Its
own words: "The TSP itself is not solved here". It knows nothing about time
windows, capacities, driver hours or skills.

**`vrp/` the package** models all of those, solves with a portfolio of engines
(PyVRP, OR-Tools) plus its own local search and ruin-and-recreate, and verifies
the result against sixteen invariants with code that shares nothing with the
solver.

They answer different questions. The gateway's is "split these stops between
these depots and give me a sensible driving order, now"; the platform's is
"produce a plan that is feasible against every constraint this operation has".

## The gateway is a port, and parity is still a live constraint

`gateway/Cargo.toml` describes the binary as a "Rust port of the FastAPI
gateway in src/app". That service is gone, but its observable behaviour is
still the specification, and several design choices only make sense in that
light:

- **Cache keys are byte-compatible with Python's** (`gateway/src/cache.rs`).
  `json.dumps` separators, `ensure_ascii` escaping and Python's float `repr`
  are all reproduced, with cross-language reference digests pinned in tests,
  because both gateways were deployed against one Redis during the transition.
- **Metric buckets match `prometheus-fastapi-instrumentator`'s defaults**
  (`gateway/src/metrics.rs`), so `histogram_quantile` agrees across the two.
- **Retry exhaustion surfaces as 500, not 503** (`gateway/src/osrm/client.rs`),
  reproducing a tenacity quirk rather than tidying it up, "because it is
  observable behaviour, and changing it would make a parity diff ambiguous".
- **Response bytes are relayed unparsed**, because a decode/re-encode cycle
  shifted about 1 ULP of some geometry coordinates.

A differential harness (`parity/`) drives both implementations over a seeded
corpus and diffs the responses. See doc 36 in the Tier 3 set.

## Request path

The router is built in `gateway/src/main.rs:148-175`. Layers are applied
innermost-first, so a request traverses them in reverse order of the calls:

```
TCP listener
  └─ catch_panic          turns a panic into 500 {"detail":"Internal server error"}
      └─ observe          counts and times the request, opens the server span
          └─ rate_limit    429 past the per-endpoint allowance
              └─ require_json_body   422 unless Content-Type is JSON
                  └─ announce_deprecation   adds Deprecation/Link/Sunset
                      └─ router → handler → OsrmClient → osrm-routed
```

This ordering is load-bearing and was confirmed by request, not read off the
source: `observe` sits outside `rate_limit`, so a shed 429 is still counted —
"otherwise the metric would go quiet exactly when the gateway is under
pressure". Two consequences are worth knowing, and both are surprising:

1. **`announce_deprecation` is innermost**, so responses produced by an outer
   layer carry no advisory. A 429, or a 422 for a missing `Content-Type`,
   arrives on the deprecated surface with no `Deprecation` header. Handler
   responses and the 404/405 fallbacks do carry it. The comment above the
   function calls it "outermost of the response-shaping layers", which is true
   only relative to the router, not to the middleware stack.
2. The same reading places `catch_panic` outside `observe`, which would mean a
   panicking request never reaches `observe`'s counter — the opposite of what
   its comment claims. Not reproduced (no panic path is reachable from a
   request), so treat it as a question for the team rather than a finding.

## Process model

One process, `WORKERS` tokio worker threads (`main.rs:47-55`). This is the
choice that deletes an entire subsystem: the Python deployment ran several
uvicorn processes and therefore needed `PROMETHEUS_MULTIPROC_DIR`, a collector
that aggregated across workers, and a directory wiped on every start. One
process means one registry and none of that machinery
(`gateway/src/metrics.rs:1-9`).

Shutdown is on SIGTERM or Ctrl-C (`main.rs:355-370`), because Docker signals
PID 1 and FreeBSD's `daemon(8)` reaps the child. On the way out the Redis
connection is closed and pending spans are flushed under a 2-second budget.

## Dependencies the architecture assumes

- **`osrm-routed`**, one instance per routing profile. `OSRM_BASE_URL` serves
  driving; `OSRM_URL_CYCLING` and `OSRM_URL_WALKING` are empty until a
  deployment stands up a graph, and a request for an unserved profile is
  refused by name rather than answered from the driving graph.
- **Redis**, optional. Absent, the L2 cache and the shared rate-limit counter
  both degrade to in-process behaviour without failing anything.
- **An OTLP collector**, optional. Absent, tracing is simply off.

## What the code does not say

- **There is no authentication or authorization anywhere in the gateway.** The
  only `auth` strings in `gateway/src/` are Swagger UI's oauth2-redirect
  boilerplate. Whether this is a trusted-network posture or a gap is a decision
  the code does not record.
- No TLS in either direction: `reqwest` is built without it deliberately, and
  Redis likewise.
- No stated SLOs. `benchmarks/baseline.json` records what a run measured, not
  what the system promises.
