# Remediation Plan — OSRM API Gateway v0.2.2

**Addressing gaps identified in FIT_GAP_ANALYSIS.md**  
Date: 2026-06-25

---

## Overview

This plan addresses 10 gaps across 4 workstreams, prioritized by impact and dependency order. Total estimated effort: **~5–6 days**.

| Priority | Workstream | Gaps | Effort |
|----------|------------|------|--------|
| P0 | Core hygiene | GAP-001 (cleanup), GAP-006 (response caching) | 0.5 day |
| P1 | Observability & resilience | GAP-002 (retry), GAP-003 (logging), GAP-004 (health), GAP-005 (metrics) | 1.5 days |
| P2 | Test coverage expansion | GAP-007 (integration), GAP-008 (VRP units), GAP-009 (GraphBuilder units) | 2–3 days |
| P3 | Documentation alignment | GAP-010 (SDD/docs) | 0.5 day |

---

## Workstream A — Core Hygiene (P0)

### A.1 GAP-001: Wire `OSRMClient.close()` into FastAPI Lifecycle

**File:** `app/main.py`

**Problem:** `osrm_client` module variable is created at module level (line 23) but `close()` is never called. The `httpx.AsyncClient` connection pool is discarded without graceful shutdown.

**Fix:** Add a FastAPI lifespan context manager that closes the client on shutdown.

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await osrm_client.close()

app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
```

**Verification:** Run the app, send a request, Ctrl+C — no `httpx` unclosed transport warnings.

**Effort:** 15 minutes

---

### A.2 GAP-006: Add Response Caching for Repeated Route/Matrix Requests

**File:** `app/services/cache.py` (new), `app/services/osrm_client.py`, `app/main.py`

**Problem:** Identical requests hit OSRM every time with no caching.

**Fix:** Add a lightweight in-memory LRU cache (or `cachetools`) for route and matrix responses. Keyed by a hash of the endpoint + params.

```python
# app/services/cache.py
from cachetools import TTLCache
from typing import Any, Dict

# 15-minute TTL, max 1024 entries
response_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=900)
```

Wrap in `OSRMClient._get`:

```python
async def _get(self, endpoint: str, params: dict = None) -> dict:
    cache_key = f"{endpoint}:{hash(frozenset((params or {}).items()))}"
    if cache_key in response_cache:
        return response_cache[cache_key]
    ...
    response_cache[cache_key] = data
    return data
```

**Dependency:** Add `cachetools` to `pyproject.toml`.

**Verification:** Call `/route` twice with identical payload; second call returns in <5ms (cached).

**Effort:** 1 hour

---

## Workstream B — Observability & Resilience (P1)

### B.1 GAP-002: Add Retry Logic for Transient OSRM Failures

**File:** `app/services/osrm_client.py`

**Problem:** No retry on 5xx or timeout. A transient OSRM blip propagates immediately to the client.

**Fix:** Use `tenacity` or manual retry wrapper in `_get()`. Apply exponential backoff for 5xx, timeout, and connection errors. HTTP 4xx should not retry.

```python
import tenacity

@staticmethod
def _retryable(exception: Exception) -> bool:
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code >= 500
    return isinstance(exception, (httpx.TimeoutException, httpx.TransportError))

# Inside _get, wrap the call:
@tenacity.retry(
    retry=tenacity.retry_if_exception(_retryable),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    stop=tenacity.stop_after_attempt(3),
)
async def _get(self, endpoint: str, params: dict = None) -> dict:
    ...
```

Alternatively, use `httpx` transport with `retries` option or a custom `BaseTransport` wrapper.

**Dependency:** Add `tenacity` to `pyproject.toml`.

**Verification:** Mock OSRM to return 503 twice then 200; confirm client succeeds on third attempt.

**Effort:** 1 hour

---

### B.2 GAP-003: Structured Logging Configuration

**File:** `app/logging_config.py` (new), `app/main.py`

**Problem:** `logging.getLogger(__name__)` with no formatters, handlers, or level configuration.

**Fix:** Configure structured JSON logging at startup for production (or use `structlog`).

```python
# app/logging_config.py
import logging
import sys

def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
```

Call `setup_logging(settings.DEBUG)` at the top of `main.py` before importing other modules.

**Verification:** Logs appear as structured lines with timestamps and levels.

**Effort:** 30 minutes

---

### B.3 GAP-004: Backend-Aware Health Check

**File:** `app/main.py`

**Problem:** `/health` returns a hardcoded response without verifying OSRM connectivity.

**Fix:** Make health check call OSRM (e.g., `/route/v1/driving/0,0;0,0` with a short timeout) and report status accordingly.

```python
@app.get("/health", tags=["System"])
async def health_check():
    osrm_healthy = True
    try:
        resp = await osrm_client._client.get(
            "/route/v1/driving/0,0;0,0",
            timeout=5.0
        )
    except Exception:
        osrm_healthy = False

    return {
        "status": "healthy" if osrm_healthy else "degraded",
        "service": settings.APP_NAME,
        "osrm_backend": "up" if osrm_healthy else "down"
    }
```

**Verification:** Start without OSRM → `/health` returns `{"status": "degraded", "osrm_backend": "down"}`.

**Effort:** 30 minutes

---

### B.4 GAP-005: Add Prometheus Metrics Endpoint

**File:** `app/main.py`, `app/metrics.py` (new)

**Problem:** No request latency, error rate, or throughput observability.

**Fix:** Add `prometheus-fastapi-instrumentator` or `starlette-exporter` to expose `/metrics`.

```python
# app/metrics.py
from prometheus_fastapi_instrumentator import Instrumentator

def setup_metrics(app):
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", tags=["System"])
```

Call `setup_metrics(app)` in `main.py` after creating the app.

Add `prometheus-fastapi-instrumentator` to `pyproject.toml`.

Alternatively, for a lightweight approach, add a middleware that counts requests per route, status, and latency, exposing at `/metrics`.

**Verification:** `curl localhost:8000/metrics` returns Prometheus-format metrics.

**Effort:** 1 hour

---

## Workstream C — Test Coverage Expansion (P2)

### C.1 GAP-009: GraphBuilder Unit Tests

**File:** `tests/test_graph_builder.py` (new)

**Test cases:**
- `test_build_from_matrix_empty`: empty matrices → graph with 0 edges
- `test_build_from_matrix_basic`: 3×3 duration+dist matrix → 6 edges, 3 nodes with correct lon/lat
- `test_build_from_matrix_missing_distances`: no distances key → edges with duration only
- `test_build_from_matrix_coordinate_metadata`: node attributes contain lon/lat from request

```python
@pytest.mark.asyncio
async def test_build_from_matrix_basic():
    matrix_data = {
        "durations": [[0, 120, 240], [120, 0, 180], [240, 180, 0]],
        "distances": [[0, 5000, 10000], [5000, 0, 8000], [10000, 8000, 0]]
    }
    request = MatrixRequest(
        coordinates=[Coordinate(lon=-84.09, lat=9.93),
                     Coordinate(lon=-84.08, lat=9.94),
                     Coordinate(lon=-84.10, lat=9.92)],
        annotations="duration,distance"
    )
    result = GraphBuilder.build_from_matrix(matrix_data, request)
    assert len(result["nodes"]) == 3
    assert len(result["edges"]) == 6
    assert result["nodes"][0]["lon"] == -84.09
```

**Verification:** `pytest tests/test_graph_builder.py -v` passes.

**Effort:** 1 hour

---

### C.2 GAP-008: VRP Allocation Unit Tests

**File:** `tests/test_vrp_allocation.py` (new)

**Test cases for `_allocate_stops` (pure logic, no I/O):**
- `test_allocate_travel_time_mode`: 2 depots, 2 stops → each depot gets one stop based on duration
- `test_allocate_distance_mode`: uses distance matrix instead of duration
- `test_allocate_radial_mode`: uses Euclidean distance only
- `test_allocate_hysteresis_prevents_flapping`: stop near boundary assigned to anchor when another depot is better by <hysteresis
- `test_allocate_hysteresis_allows_change`: stop assigned to better depot when margin > hysteresis
- `test_allocate_sanity_override`: best depot is >50km Euclidean away → uses anchor
- `test_allocate_max_radius`: stop beyond max_radius → unreachable
- `test_allocate_single_depot`: all stops assigned to the only depot
- `test_allocate_all_unreachable`: no depots within radius → all unreachable
- `test_allocate_infinity_handling`: some depot→stop pairs have infinite cost → fallback to anchor

**Effort:** 1.5 hours

---

### C.3 GAP-007: Integration Tests Against Real OSRM Backend

**File:** `tests/test_integration.py` (new)

**Problem:** All existing tests mock `OSRMClient`. No test validates behavior against a real OSRM response (even if running in CI).

**Test cases:**
- `test_route_endpoint_integration`: POST /route with real coords → 200 with valid geojson
- `test_matrix_endpoint_integration`: POST /matrix → 200 with durations+distances
- `test_health_endpoint_integration`: GET /health → 200
- `test_vrp_basic_integration`: 1 depot + 3 stops → VrpResponse with 1 route, correct geometry
- `test_vrp_unreachable_stops`: stop on island (if available in data) → reported as unreachable

**Prerequisite:** A running OSRM backend. Use `@pytest.mark.integration` to skip these tests when no backend is available.

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_route_endpoint_integration():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as c:
        resp = await c.post("/route", json={
            "origin": {"longitude": -84.0907, "latitude": 9.9281},
            "destination": {"longitude": -84.0833, "latitude": 9.9333}
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "routes" in data
    assert len(data["routes"]) > 0
```

**Effort:** 1.5 hours

---

### C.4 Additional Test Targets (outside FIT_GAP but recommended)

**File:** `tests/test_config.py` (new), `tests/test_error_scenarios.py` (new)

- `test_config_defaults`: verify Settings default values
- `test_config_env_override`: set env vars → verify overrides
- `test_osrm_down_propagation`: mock client to raise `ConnectError` → endpoint returns 500
- `test_malformed_payload`: POST /route with missing `origin` → 422
- `test_rate_limit_exceeded`: rapid requests → 429 response
- `test_vrp_edge_empty_stops`: (if handled) → 422 or empty response
- `test_match_trace_splitting`: GPS trace with gap > threshold → multiple matchings

**Effort:** 1 hour

---

## Workstream D — Documentation Alignment (P3)

### D.1 GAP-010: Align SDD with Code Reality

**File:** `docs/SDD.md`

**Change:** Update DEC-001 to remove `close()` from the public interface table, or add a note that it exists but requires a lifespan integration.

**Verification:** SDD matches all 18 class methods actually implemented.

**Effort:** 15 minutes

---

## Implementation Order & Dependencies

```
Day 1   │ P0: A.1 Cleanup  ──────────────────┐
        │ P0: A.2 Caching    ────────────────┤
        └─────────────────────────────────────┘
Day 2   │ P1: B.1 Retry       ───────────────┐
        │ P1: B.2 Logging     ───────────────┤
        │ P1: B.3 Health Check ──────────────┤
        │ P1: B.4 Metrics      ──────────────┤
        └─────────────────────────────────────┘
Day 3-4 │ P2: C.1 GraphBuilder tests ───────┐
        │ P2: C.2 VRP Allocation tests ─────┤
        │ P2: C.4 Additional unit tests ────┤
        └─────────────────────────────────────┘
Day 5   │ P2: C.3 Integration tests ────────┐
        └─────────────────────────────────────┘
Day 6   │ P3: D.1 SDD docs fix ─────────────┐
        │ Run full test suite, verify all    │
        └─────────────────────────────────────┘
```

**Dependencies:**
- B.4 (Metrics) uses `app` instance → after A.1 (which creates `app`)
- C.3 (Integration) requires `docker compose up` with a running OSRM backend → can run last
- C.1, C.2 (unit tests) have no code dependencies → can run in parallel on Day 3

---

## Summary

| Gap | Workstream | Effort | Deliverable |
|-----|------------|--------|-------------|
| GAP-001 | Core hygiene | 15 min | Lifespan hook in `main.py` |
| GAP-006 | Core hygiene | 1 h | `TTLCache` wrapper in `osrm_client.py` |
| GAP-002 | Observability & resilience | 1 h | Retry decorator with exponential backoff |
| GAP-003 | Observability & resilience | 30 min | `logging_config.py` with structured format |
| GAP-004 | Observability & resilience | 30 min | Backend-aware `/health` endpoint |
| GAP-005 | Observability & resilience | 1 h | `/metrics` endpoint via Prometheus instrumentation |
| GAP-009 | Test coverage | 1 h | `test_graph_builder.py` (4 tests) |
| GAP-008 | Test coverage | 1.5 h | `test_vrp_allocation.py` (10 tests) |
| GAP-007 | Test coverage | 1.5 h | `test_integration.py` (5 tests, marked `integration`) |
| + | Test coverage (bonus) | 1 h | `test_config.py`, `test_error_scenarios.py` (7 tests) |
| GAP-010 | Documentation | 15 min | SDD alignment patch |
| **Total** | | **~5–6 days** | **26+ new tests + 6 code changes** |
