# Scaling Readiness Plan

What breaks in this codebase before the hardware does.

Everything here was measured against the FreeBSD jail deployment (2 cores,
2 GB shared with two other jails, single uvicorn worker) using
`make loadtest`. The numbers are the evidence, not estimates.

## Measured baseline

| Workload | Result |
|---|---|
| `mixed` @ 30/s for 25s, all 11 endpoints | 751 requests, **0 errors**, p50 53 ms, p95 91 ms |
| `route` uncached, 20-way concurrency | ~150 req/s, p50 82 ms |
| `route` cached (same payload) | 1–9 ms — a 10–40× multiplier |
| `route` @ 100/s for 8s | 547×200, 254×429 — rate limiter engages correctly |
| 400 sequential `/route` | RSS flat at 139 MB — no leak |
| `/vrp` 500 / 1000 / 2000 stops | peak RSS 171 / 206 / 277 MB, 0.9 / 0.8 / 1.6 s |
| 4 concurrent distinct 2000-stop `/vrp` | RSS 307 → 615 MB in 5s, host free memory 506 → 83 MB |

Read together: the service is healthy and fast at its current scale, request
*count* costs nothing, and the danger is payload size × concurrency.

## Status

P0 is done and verified on the jail deployment (2026-08-20). P1 is implemented
and unit-tested but its jail acceptance (stop the engine, watch `/ready` flip to
503 and back) has not been run yet. P2 is open.

Verifying P0-2 turned up a defect this plan had not predicted: Redis was
unreachable from the jail, so both the L2 cache and the limiter's shared storage
had been silently inert. See "Redis needs the jail to have a real loopback" in
`../deployment_freebsd.md`.

| Item | Status | Evidence |
|---|---|---|
| P0-1 metrics | done | 201 requests, counter delta 201 across 2 workers (was ~100) |
| P0-2 limiter | done | 800 requests/8s at 600/min: 549 allowed with Redis vs 772 without |
| P0-3 workers | done | `osrm_api_gateway_workers`, two workers under `daemon(8)` |
| P1 readiness | done | `/ready` implemented; all three probes use it (`Makefile`, `Dockerfile`, `install.sh`) |
| Proxy-aware limits | done | `--forwarded-allow-ips` on both paths; without it every client behind a balancer shares one bucket |
| Docker workers | done | `WORKERS` via `deploy/docker/entrypoint.sh`; P0-1/2/3 previously applied to the jail only |
| P2-1 payload caps | open | |
| P2-2 executor | open | |
| P2-3 matrix bound | open | |

## Ordering

P0 items are prerequisites for running more than one process. P1 is a
prerequisite for putting a load balancer in front. P2 protects a node from a
single expensive request. Do them in that order — P0 items are what make
`--workers` and multi-node deployment correct rather than merely possible.

---

## P0-1 — Prometheus metrics break under multiple workers

**Breaks when:** `uvicorn --workers N`, i.e. the first step of vertical scaling.

**Cause:** `prometheus_client` keeps counters in process memory. Each worker
answers `/metrics` with only the requests it happened to serve, so a scrape hits
a random worker and reports roughly `1/N` of the traffic. Rates and percentiles
are silently wrong — worse than no metrics, because they look plausible.

**Fix:** multiprocess mode in `app/metrics.py` — a `PROMETHEUS_MULTIPROC_DIR`
setting, `CollectorRegistry` + `MultiProcessCollector` when it is set, and
cleanup of the directory at startup. Single-worker behaviour must stay
unchanged when the setting is empty.

**Acceptance:** with `--workers 2`, `make loadtest LOADTEST_SCENARIO=route
LOADTEST_RATE=20 LOADTEST_DURATION=30` then scrape `/metrics`;
`http_requests_total` must equal the generator's completed count (±1 scrape
window), not half of it.

## P0-2 — The rate limiter is per process

**Breaks when:** `--workers N`, and again per node in a fleet.

**Cause:** `Limiter(key_func=get_remote_address)` in `app/main.py` defaults to
in-memory storage. With N workers the effective limit becomes `N ×
RATE_LIMIT_*`; across M nodes behind a balancer, `N × M ×`. The limits still
*work* (verified above), they just stop meaning what the settings say.

**Fix:** pass `storage_uri=settings.REDIS_URL` to `Limiter` when it is set,
falling back to in-memory when it is not, so local development and tests are
unaffected. Redis is already a dependency and already deployed.

**Acceptance:** `--workers 2`, then `route` at 100/s for 8s must produce the
same ~600 successes per minute as the single-worker run above, not ~1200.

## P0-3 — The rc.d script hardcodes one worker

**Breaks when:** you want to use the second core. Enabler for P0-1 and P0-2.

**Cause:** `deploy/freebsd/osrm-api-gateway` builds a fixed
`uvicorn app.main:app --host … --port …` command line.

**Fix:** add `osrm_api_gateway_workers` (default 1, so current behaviour is
unchanged), appending `--workers N` when greater than 1. `install.sh` sets it
via `sysrc`; document it alongside the other knobs.

**Acceptance:** `sysrc osrm_api_gateway_workers=2 && service osrm_api_gateway
restart` shows two worker processes under the `daemon(8)` supervisor, and
`mixed` load still returns 0 errors.

## P1 — `/health` returns 200 while degraded

**Breaks when:** a load balancer needs to drain a node whose engine is down.

**Cause:** `/health` reports `{"status": "degraded", "osrm_backend": "down"}`
with HTTP **200**. Every balancer treats that as healthy and keeps sending
traffic to a node that cannot route. The deploy-time `phase_health` check has
the same blind spot and only passes today because it probes OSRM separately.

**Fix:** add `/ready` that returns 503 when `osrm_client.ping()` fails, leaving
`/health` untouched for humans and existing dashboards. Point `phase_health` and
any balancer config at `/ready`.

**Acceptance:** `service osrm_routed stop` → `/ready` returns 503 within one
`HEALTH_CHECK_TIMEOUT`, `/health` still returns 200 with `degraded`; restart the
engine and `/ready` returns to 200.

## P2-1 — One expensive request can take the node down

**Breaks when:** a few large VRP requests arrive together. Measured: four
concurrent 2000-stop solves consumed 423 MB of host memory in 5 seconds, and
the schema permits **10 000** stops per request — roughly 700 MB peak for a
single call on a 2 GB box.

**Cause:** `VrpRequest.stops` allows `max_length=10000` with no relationship to
the memory that implies, and every solve runs to completion inside the request.

**Fix, in two parts:**

1. A `VRP_MAX_STOPS` setting enforced in the schema, defaulted to something the
   deployment can actually survive (2000 on this host), returning 422 rather
   than an OOM. Cheap, and it is the part that prevents the outage.
2. Bounded concurrency for the heavy endpoints — an `asyncio.Semaphore` sized by
   setting, so request N+1 queues instead of multiplying peak memory. Returning
   503 when the queue is full is preferable to being killed.

**Acceptance:** `make loadtest LOADTEST_SCENARIO=vrp LOADTEST_RATE=4
LOADTEST_ARGS="--size 2000"` keeps gateway RSS under a stated ceiling and
returns 422/503 rather than dying; the single-request path is unchanged.

## P2-2 — CPU-bound solves block the event loop

**Breaks when:** VRP traffic and routing traffic share a worker. A 1.6 s solve
is 1.6 s during which that worker serves no `/route` request. The `mixed` run
already shows the asymmetry: `/vrp` p99 366 ms against `/route` p95 85 ms.

**Cause:** `vrp_service` does numpy/networkx work directly in the coroutine.

**Fix:** run the solve in a `ProcessPoolExecutor` via `run_in_executor`, or
deploy the optimisation endpoints as their own jail with its own worker count.
The process pool is the smaller change and keeps one deployment artefact.

**Acceptance:** under `mixed` load with `--size 2000` VRP requests present,
`/route` p95 stays within 2× of its no-VRP baseline.

## P2-3 — `/matrix` advertises 50× what the engine accepts

**Breaks when:** a client trusts the schema. `MatrixRequest.coordinates` allows
`max_length=5000`; `osrm-routed` refuses anything over 100 with
`{"code":"TooBig"}` because `--max-table-size` defaults to 100.

**Fix:** either raise `--max-table-size` on the engine (rc.d script and
`deploy/docker/docker-compose.yml` together — they must not diverge) or lower the schema bound
to match and reject early with 422. Decide which, then make both paths agree.

**Acceptance:** the documented maximum and the enforced maximum are the same
number, and `make loadtest LOADTEST_SCENARIO=matrix LOADTEST_ARGS="--size 200"`
returns a consistent result rather than a pass-through 400.

---

## Not in this plan

**Caching.** Raising `L1_CACHE_MAXSIZE` / `REDIS_MAXSIZE` from 1024 and
quantising coordinates to a grid is the single largest throughput lever
available (10–40× on repeat traffic), but it is tuning, not breakage, and it
belongs to a capacity exercise with real traffic patterns rather than to this
list.

**Horizontal deployment** — replicating the read-only `.osrm.*` data across
nodes, a balancer tier, shared Redis. That work is straightforward once P0 and
P1 are done, and pointless before.
