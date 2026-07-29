# Changelog

All notable changes to this project will be documented in this file.

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
