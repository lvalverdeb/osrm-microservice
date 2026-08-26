# Software Design Description

## For the OSRM API Gateway

**Version 1.0.0** — describes the Rust gateway in `gateway/`
Supersedes v0.3.0, which described the FastAPI implementation removed in August 2026.

---

## Table of contents

- [1. Introduction](#1-introduction)
- [2. Design overview](#2-design-overview)
- [3. Design views](#3-design-views)
- [4. Decisions and rationale](#4-decisions-and-rationale)
- [5. Quality attributes](#5-quality-attributes)
- [6. Constraints and known limits](#6-constraints-and-known-limits)

---

## 1. Introduction

### 1.1 Purpose

This document describes how the OSRM API Gateway is built and why. It is the
design reference; [RUNBOOK.md](RUNBOOK.md) is the operational one and
[API_REFERENCE.md](API_REFERENCE.md) is the contract.

### 1.2 Scope

The gateway is a single Rust binary that fronts `osrm-routed`. It:

- exposes eleven HTTP endpoints, six of which relay an OSRM service — five
  returning JSON and one returning protobuf tiles;
- computes two things OSRM does not — a node-link graph from a travel matrix,
  and a vehicle-routing solution built from allocation plus per-vehicle
  sequencing;
- adds caching, rate limiting, metrics, tracing and admission control around
  both.

It does **not** own map data, and it holds no persistent state of its own. Redis
is a cache, not a database; losing it costs cache hits and nothing else.

### 1.3 Definitions

| Term | Meaning |
|---|---|
| Engine | `osrm-routed`, the C++ routing backend |
| Upstream | A request from the gateway to the engine |
| L1 / L2 | In-process cache tier / shared Redis tier |
| Allocation | Assigning stops to depots |
| Sequencing | Ordering one vehicle's stops, delegated to OSRM `/trip` |
| Chunk | One vehicle's load: a contiguous slice of a depot's stops |

### 1.4 References

| Document | Role |
|---|---|
| [API_REFERENCE.md](API_REFERENCE.md) | Request and response shapes |
| [configuration.md](configuration.md) | All 35 settings and where each is read |
| [deployment.md](deployment.md) | Both deployment options |
| [features/](features/) | Caching, rate limiting, observability, clustering modes |
| [planning/SCALING_READINESS_PLAN.md](planning/SCALING_READINESS_PLAN.md) | Measured scaling work and its outcomes |

---

## 2. Design overview

### 2.1 Context

```
  client                     gateway (this)                   engine
┌──────────┐          ┌───────────────────────────┐      ┌──────────────┐
│ app /    │  JSON    │ validate → rate limit →    │ HTTP │ osrm-routed  │
│ browser  │ ───────► │ cache → upstream → relay   │ ───► │ (C++)        │
└──────────┘          └───────────┬───────────────┘      └──────────────┘
                                  │
                            ┌─────▼─────┐
                            │  Redis    │  L2 cache + rate-limit counters
                            └───────────┘
```

Clients never reach the engine directly. The gateway translates a JSON request
into OSRM's URL-and-query form, and relays the engine's response bytes
unaltered — which is both faster than re-encoding and the only way to guarantee
the numbers a client receives are the numbers the engine produced.

### 2.2 Stakeholder concerns

| Concern | Whose | Addressed by |
|---|---|---|
| Stable request contract | Client developers | §3.2, [API_REFERENCE.md](API_REFERENCE.md) |
| The engine is not overwhelmed | Operators | §3.5 rate limiting, §3.6 admission control |
| A bad request is diagnosable | Client developers | §3.3 validation, error shapes |
| Failures are visible | Operators | §3.7 observability |
| It fits a 2 GB shared jail | Operators | §5 memory, §6 constraints |

### 2.3 Module structure

7,180 lines across 19 modules. Grouped by responsibility:

| Group | Modules | Responsibility |
|---|---|---|
| Entry | `main.rs` | Runtime, router, middleware stack, shutdown |
| Contract | `models.rs`, `openapi.rs`, `error.rs` | Request types, validation, schema, error bodies |
| Handlers | `handlers.rs` | One function per endpoint; graph construction |
| Upstream | `osrm/client.rs`, `osrm/params.rs` | Retry, cache lookup, query construction |
| Caching | `cache.rs`, `redis_cache.rs`, `pyfloat.rs` | Key construction, both tiers |
| Optimisation | `vrp/allocate.rs`, `vrp/solve.rs` | Allocation, chunking, fan-out |
| Cross-cutting | `ratelimit.rs`, `admission.rs`, `metrics.rs`, `telemetry.rs`, `config.rs` | Limits, shedding, instrumentation, settings |

`models.rs` is the largest at 1,222 lines because it carries the whole
validation surface, which is deliberate: validation is a contract, and a
contract belongs in one readable place.

---

## 3. Design views

### 3.1 Request pipeline

Middleware runs outside in. Order is load-bearing:

```
  observe  ──►  rate limit  ──►  require JSON  ──►  catch panic  ──►  handler
  (metrics,        (429)           (422 on a         (500 instead
   span)                            bad media        of a dropped
                                    type)            connection)
```

`observe` sits outermost so a shed 429 is still counted — otherwise the metric
goes quiet exactly when the gateway is under pressure. `catch_panic` sits
innermost so a panic still passes back through `observe` and is recorded as the
500 it became.

### 3.2 Endpoints

| Endpoint | Kind | Notes |
|---|---|---|
| `/route`, `/matrix`, `/match`, `/trip`, `/nearest` | Relay | Response bytes passed through unaltered |
| `/tile/{profile}/{z}/{x}/{y}` | Relay | Protobuf; no cache, no retry |
| `/matrix-graph` | Computed | Matrix → node-link graph |
| `/vrp`, `/vrp/allocate` | Computed | Allocation and sequencing |
| `/health`, `/ready` | Probe | `/health` always 200 with detail; `/ready` 503 when the engine is down, so a balancer drains the node |
| `/docs`, `/redoc`, `/openapi.json` | Schema | Generated from the types that serve requests |

### 3.3 Validation

Every request is decoded and validated before anything else happens. The 422
body reproduces pydantic's entry shape field for field — `type`, `loc`, `msg`,
`input`, `ctx` — because clients were written against it.

Two properties are less obvious than they look:

- **Decoding collects every failure, not the first.** serde stops at the first
  problem; each is patched with a value its field accepts and the document
  re-read, so a body wrong in three places reports three errors. The patched
  copy is a throwaway — `input` is always filled from what the caller sent.
- **Coercion follows pydantic's lax mode.** `"-84.09"` is a valid float, `35.0`
  a valid integer, `35.5` is not. This is not laxity for its own sake: it is the
  contract clients already depend on.

### 3.4 Caching

Two tiers, cache-aside, checked L1 then L2. A key is the endpoint path plus a
SHA-256 of the sorted parameters, byte-reproducing Python's
`json.dumps(..., sort_keys=True)` so both implementations address the same
entries — which is why `pyfloat.rs` exists at all.

Redis errors are always swallowed and its timeouts bounded hard. See
[features/caching.md](features/caching.md).

### 3.5 Rate limiting

Fixed-window counting per client IP, per endpoint bucket. Counted in Redis when
`REDIS_URL` is set so a fleet shares one allowance, falling back to an
in-process map when it is not or when Redis does not answer. An unparseable
limit stops the process at startup rather than coming up unlimited.

### 3.6 Optimisation and admission control

`/vrp` runs in two phases:

1. **Allocate.** A depot-to-stop matrix, then per stop: nearest depot by road
   cost, with a Euclidean anchor tiebreak, a hysteresis band that keeps
   territories stable between runs, and a sanity override that refuses an
   implausible matrix answer.
2. **Sequence.** Each depot's stops are ordered by sweep angle about the depot,
   cut into vehicle loads bounded by `min(VRP_CHUNK_SIZE, capacity)` and the
   `/trip` coordinate cap, then each load is sequenced by OSRM `/trip`. Chunks
   fan out concurrently; the first failure cancels its siblings.

Solves are expensive in memory, so an admission gate bounds how many run at
once (`VRP_MAX_CONCURRENCY`), with a wait budget (`VRP_QUEUE_TIMEOUT`) and an
optional queue-depth bound. Shed requests get 503 with `Retry-After`.

### 3.7 Observability

Prometheus metrics with the names, types, labels and bucket boundaries the
previous implementation exposed, so dashboards survived the port. A server span
per request and a client span per upstream call, with W3C trace context
extracted inbound and injected outbound. Logging keeps Python's line format,
with colour off because both deployments write to a file.

See [features/observability.md](features/observability.md).

---

## 4. Decisions and rationale

| # | Decision | Rationale |
|---|---|---|
| D-1 | Relay engine bytes rather than decode and re-encode | Guarantees the caller's numbers are the engine's. Re-encoding shifted the last ULP on some coordinates |
| D-2 | `WORKERS` is tokio worker threads, not processes | One registry sees all traffic, so the multiprocess metrics machinery, its directory and its wipe-on-start all disappear |
| D-3 | Cache keys byte-reproduce Python's `json.dumps` | Lets both implementations share one Redis during a transition, and pins a format that would otherwise drift silently |
| D-4 | Validation reproduces pydantic's error shape | Clients branch on `type` and `loc`; changing them is a breaking change with no signal |
| D-5 | Sequencing delegates to OSRM `/trip` | The engine already solves the TSP well. Writing another is the expensive way to do worse |
| D-6 | Upstream URL length is checked before sending | The engine drops the connection past ~24,750 bytes, which retries turn into a 500. A 422 naming the limit tells the caller what to change |
| D-7 | An unparseable rate limit is fatal at startup | An endpoint that comes up silently unlimited is the one failure a limiter must not have. Other malformed settings fall back to their default, which is benign |
| D-8 | Redis is optional everywhere it appears | The jail ran for weeks with Redis unreachable and served traffic throughout |

Two deliberate deviations from the previous implementation, both recorded
because they are visible to callers:

- `/vrp` refuses a request needing an over-long upstream URL (D-6). The old
  gateway built the same URL and failed the same way, without saying why.
- `generate_hints` is exposed on the routing endpoints. It has no counterpart in
  the old implementation; new surface, not a port gap.

---

## 5. Quality attributes

| Attribute | Position |
|---|---|
| Latency | Measured on the jail: no meaningful difference from the previous implementation on ordinary traffic, because the engine is the constraint. ~2× faster on cache hits, where the gateway is the whole request |
| Memory | ~57 MB under load against ~221 MB. This is what makes a 2 GB shared jail comfortable |
| Throughput | ~3× per-worker headroom over the previous implementation, unspendable while the engine saturates first |
| Determinism | Same input, same plan. Ties break to the lowest index |
| Dependencies | 17 direct crates, no TLS stack — the engine is reached over loopback or a compose network |

Figures are from [planning/SCALING_READINESS_PLAN.md](planning/SCALING_READINESS_PLAN.md),
measured on the jail rather than argued about.

---

## 6. Constraints and known limits

- **One engine, one profile.** `osrm-routed` serves a single profile fixed at
  extract time, and it ignores the profile segment in the URL entirely. A
  heterogeneous fleet needs several engines.
- **Coordinates travel in the URL.** That is OSRM's contract, and it caps a
  request at roughly 720 `/match` breadcrumbs. Polyline-encoded input would
  raise the ceiling and is not yet built.
- **No time dimension.** OSRM has no departure-time parameter, so every plan
  assumes free-flow speed. This bounds what the VRP can honestly claim.
- **`process_*` metrics are Linux-only.** They are read from `/proc`, so the
  FreeBSD jail reports none.
- **The VRP is capacity and geography only.** No time windows, service times,
  skills or shifts. See
  [planning/VRP_SDD_FIT_GAP.md](planning/VRP_SDD_FIT_GAP.md) for the measured
  distance between this and a full fleet-optimisation platform.
