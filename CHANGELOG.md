# Changelog

All notable changes to this project will be documented in this file.

## [v1.0.0] — 2026-08-25

The gateway was rewritten in Rust. The FastAPI application it replaces was removed
in the same cycle, and the `osrm-api-gateway` PyPI package was discontinued at
0.2.1 — the gateway is now a binary built from `gateway/`, not a wheel. The port
was held to behavioural equality with the Python implementation rather than to a
redesign: same paths, same status codes, same error bodies, same metric names.

### Added

- **Rust gateway** (`gateway/`): an async axum binary serving sixteen routes — six relaying an OSRM service, three computing (`/matrix-graph`, `/vrp`, `/vrp/allocate`), three infrastructure, four documentation.
- **Readiness probe** `GET /ready`: the same OSRM check as `/health`, but the HTTP status carries the verdict (200 up, 503 down), so a load balancer drains the node instead of routing to an engine that cannot answer.
- **Admission control** (`admission.rs`): once the solve queue is too deep, VRP requests are refused immediately with `503` and a `Retry-After` header rather than queued behind work that will time out.
- **OpenAPI generated from the source types** via `utoipa`, served at `/openapi.json` and rendered at `/docs` and `/redoc`.
- **Differential parity harness** (`parity/`): diffs both gateways' responses over a seeded corpus, with recorded upstream fixtures so it runs with no engine present.
- **Load generator** (`loadtest/`): drives a running gateway at a fixed arrival rate rather than in lockstep, so a slow server shows as rising latency instead of a quietly lower rate. Per-endpoint scenarios plus a weighted `mixed` blend, and thresholds that make a run a pass/fail gate.
- **FreeBSD jail deployment** (`deploy/freebsd/`): the same three services run natively from packages and rc.d scripts, because a jail shares the FreeBSD kernel and cannot run Docker.
- **Routing tests over a hand-built map** against a real engine, so behaviour is checked against OSRM rather than against a mock.
- **Upstream URL guard**: a request needing a longer engine URL than the engine accepts is refused up front (`OSRM_MAX_URL_BYTES`, default 24000) instead of failing opaquely upstream.
- **Unreachable stops are reported** in the VRP response instead of surfacing as a 500.
- Callers can turn off OSRM's per-waypoint `hints`.

### Changed

- **Engine responses are relayed verbatim.** `OSRMClient::get` returns the raw bytes; there is no decode/re-encode cycle, so response numbers are never respelled.
- **Floats are formatted as Python spells them** (`pyfloat.rs`): `repr(9.0)` is `"9.0"` and `repr(1e-7)` is `"1e-07"`, where Rust's `Display` writes `"9"` and `"1e-7"`. OSRM accepts either, so the only symptom of getting this wrong was a cache key that never matched the Python gateway's.
- **Validation reproduces pydantic's 422 body** field for field — `type`, `loc`, `msg` — and its lax coercion, so clients branching on those keys keep working.
- **Metrics keep their old identity**: the middleware in `metrics.rs` emits the names, types, labels and bucket boundaries `prometheus-fastapi-instrumentator` produced, so existing dashboards and alerts keep working.
- **Tracing is hand-instrumented.** The `observe` middleware opens one `http.server` span per request, adopts the caller's context when one is sent, and injects `traceparent` into outbound engine calls — there is no auto-instrumentation layer to do it.
- **VRP chunking orders each depot's stops by sweep angle** before cutting them into vehicle loads, so a load is a contiguous wedge rather than an artefact of submission order.

### Removed

- **The FastAPI gateway** (`src/app/`) and its supporting modules. Documents referring to `src/app/`, `osrm_client.py`, `graph_builder.py` or `vrp_service.py` predate the port.
- **The PyPI distribution.** [0.2.1](https://pypi.org/project/osrm-api-gateway/0.2.1/) is the final release; existing installs keep working, but there will be no further ones. `pyproject.toml` now packages the development tooling only.

### Fixed

Defects found by auditing the port against the Python source, in tiers:

- The `loc` shape in 422 bodies, and rejection of malformed tile paths.
- Inputs pydantic accepted are accepted, and the ones it rejected are rejected — including the four boolean spellings `pydantic-settings` honours.
- Panics are contained and reported as the 500 they became, with the request still counted.
- The published enum values are the ones the API actually accepts.
- Constraints the API enforces are documented, and enforced on VRP as well.

---

## [v0.3.0] — 2026-06-25

### Added

- **OpenTelemetry distributed tracing** (`app/tracing.py`) with W3C TraceContext propagation, FastAPI + httpx auto-instrumentation, and OTLP export.
- **Redis-backed distributed L2 cache** (`app/services/redis_cache.py`) with Cache-Aside pattern: L1 (in-memory TTLCache) → L2 (Redis) → OSRM.
- **Agent guidelines** (`AGENTS.md`, `CLAUDE.md`) defining mandates, tech stack, code style, and output format for AI developer agents.
- Documentation: `docs/features/caching.md`, `docs/features/clustering_modes.md`, `docs/features/observability.md`, `docs/features/rate_limiting.md`, `docs/configuration.md`.

### Changed

- **Redis L2 integration**: `OSRMClient._get()` now checks L1 → L2 → OSRM with automatic population of both layers on miss.
- **FastAPI lifespan** now closes both `OSRMClient` and `RedisCache` on shutdown.
- **SDD** updated to v0.3.0 with DEC-012 (OpenTelemetry) and DEC-013 (Redis cache) decisions.

---

## [v0.2.2] — 2026-04-22

### Added

- **Full OSRM API coverage**: All six OSRM services (Route, Table, Match, Trip, Nearest, Tile) exposed through 10 FastAPI endpoints.
- **Multi-modal support**: `profile` parameter (`driving`, `cycling`, `walking`) on all routing endpoints.
- **Advanced routing options**: `overview`, `geometries`, `steps`, `annotations`, `continue_straight` exposed on Route, Match, and Trip.
- **Shared routing constraints**: `bearings`, `radiuses`, `hints`, `approaches`, `exclude`, `snapping`, `skip_waypoints` via `CommonRoutingOptions` base class.
- **Nearest service**: `POST /nearest` for road-network snapping.
- **Tile proxy**: `GET /tile/{profile}/{z}/{x}/{y}.mvt` for Mapbox Vector Tiles.
- **Structured error handling**: OSRM `code` and `message` forwarded in error responses.
- **Retry with exponential backoff**: `tenacity` retry on 5xx, timeouts, and transport errors (3 attempts, 1s–10s backoff).
- **Response caching**: `cachetools.TTLCache` with 15-minute TTL and 1024-entry limit.
- **Prometheus metrics**: `GET /metrics` via `prometheus-fastapi-instrumentator`.
- **Structured logging**: `logging_config.py` with timestamp, level, and module formatting.
- **OSRM-aware health check**: `GET /health` probes the OSRM backend and returns `healthy`/`degraded`.
- **Integration tests**: 5 tests against real OSRM backend (run via `--run-integration`).
- **VRP allocation unit tests**: 10 tests covering hysteresis, sanity, radial, radius, infinity, and single-depot scenarios.
- **GraphBuilder unit tests**: 4 tests for matrix-to-graph conversion.
- **Config and error scenario tests**: Settings validation, env overrides, OSRM-down propagation, malformed payloads.
- **Advanced example scripts**: `route_advanced_options.py`, `matrix_graph_example.py`, `error_handling_demo.py`, `clustering_mode_comparison.py`, `hysteresis_demo.py`, `health_and_metrics.py`.

### Changed

- Removed hardcoded `driving` profile from all OSRM URL construction — now schema-driven.
- `get_route()` method signature changed to accept full `RouteRequest` (one call site in `main.py` updated atomically).
- `VrpRequest` and `TripRequest` models now include all advanced OSRM parameters with backward-compatible defaults.
- Error responses from OSRM are now forwarded with structured `code`/`message` instead of generic strings.

---

## [v0.2.0] — 2026-03-15

### Added

- **VRP solver** with Location-Allocation clustering and chunked TSP via OSRM `/trip`.
- **VRP custom IDs**: Stops can include an `id` field (string or integer) propagated through allocation results.
- **`POST /vrp/allocate`** endpoint for standalone allocation without routing.
- **Clustering modes**: `travel_time`, `distance`, and `radial` with hysteresis buffer.
- **Matrix-to-graph conversion** (`POST /matrix-graph`) via `NetworkX` integration.
- **Visualization tools**: Route plotting, TSP comparison, VRP multi-depot maps, stress tests.
- Multi-language README files (English, Spanish, French).

### Changed

- Refactored `VrpAllocationResponse` to handle heterogeneous types (`Union[str, int]`) for stop identifiers and depot keys.

---

## [v0.1.0] — 2026-02-01

### Added

- Initial release: FastAPI gateway wrapping OSRM `/route`, `/table`, `/match`, `/trip` endpoints.
- Docker Compose deployment with multi-stage `Dockerfile.osrm`.
- OSM data processing pipeline (`Dockerfile.builder`).
- Costa Rica map data support.
