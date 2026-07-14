# Remediation Plan v2 — OSRM API Gateway v0.3.0

**Addressing GAP-011 (Tracing) and GAP-012 (Redis Cache)**  
Date: 2026-06-25

---

## Overview

This plan addresses 2 gaps identified in the v0.3.0 architecture expansion, focused on making the gateway suitable for horizontally-scaled controlled infrastructure.

| Priority | Workstream | Gaps | Effort |
|----------|------------|------|--------|
| P1 | Observability — Distributed Tracing | GAP-011 | 1 day |
| P2 | Scalability — Redis Cache | GAP-012 | 1.5 days |

---

## Workstream P1 — OpenTelemetry Tracing (GAP-011)

### Problem

The system spans two services (API Gateway + OSRM Backend) and makes multiple outbound HTTP calls per request. There is no way to:
- Trace a single request's path across the gateway and OSRM
- Measure per-span latency breakdowns (gateway processing vs. OSRM routing)
- Correlate slow requests with specific OSRM endpoints

Prometheus metrics (DEC-010) show aggregate rates but cannot pinpoint individual slow requests.

### Solution

Add OpenTelemetry distributed tracing with W3C TraceContext propagation.

#### Files to create/modify

| File | Action | Purpose |
|------|--------|---------|
| `app/tracing.py` | **Create** | OpenTelemetry setup — FastAPI middleware, httpx instrumentor, OTLP exporter |
| `app/main.py` | Modify | Call `setup_tracing(app)` during app initialization |
| `pyproject.toml` | Modify | Add OpenTelemetry dependencies |
| `docker-compose.yml` | Optional | Add `OTLP_ENDPOINT` env var |

#### Design: `app/tracing.py`

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from app.config import settings


def setup_tracing(app, service_name: str = "osrm-api-gateway"):
    provider = TracerProvider()
    if settings.OTLP_ENDPOINT:
        exporter = OTLPSpanExporter(endpoint=settings.OTLP_ENDPOINT)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
```

**Key behaviors:**
- Tracing is **optional**: if `OTLP_ENDPOINT` is empty/unreachable, spans are dropped silently
- `FastAPIInstrumentor` creates a root span per request with HTTP method, path, status code
- `HTTPXClientInstrumentor` creates child spans for each outbound call to OSRM
- W3C `traceparent` header is automatically propagated to OSRM Backend

#### Configuration

Add to `app/config.py`:
```python
OTLP_ENDPOINT: str = ""
```

#### Dependencies (`pyproject.toml`)

```
opentelemetry-sdk>=1.30.0
opentelemetry-instrumentation-fastapi>=0.51b0
opentelemetry-instrumentation-httpx>=0.51b0
opentelemetry-exporter-otlp-proto-http>=1.30.0
```

#### Verification

1. Start the app without `OTLP_ENDPOINT` — app starts, requests succeed, no trace export errors
2. Start with `OTLP_ENDPOINT=http://localhost:4318/v1/traces` — spans are exported
3. Make a `/route` request — verify a trace with 2 spans: `POST /route` (root) + `GET /route/v1/driving/...` (child)
4. Make a `/vrp` request — verify multiple child spans (one per matrix batch, one per `/trip` chunk)

#### Effort: 1 day

---

## Workstream P2 — Redis-Backed Distributed Cache (GAP-012)

### Problem

The current `cachetools.TTLCache` (DEC-009) is:
- **Lost on restart** — every container restart starts with a cold cache
- **Not shared** — with multiple `api` replicas, each has an independent, cold cache
- **Fixed size** — 1024 entries, cannot grow with workload

Running as part of controlled infrastructure with horizontal scaling requires a shared, durable cache layer.

### Solution

Add Redis as an L2 cache behind the existing in-memory L1, following the Cache-Aside pattern.

#### Files to create/modify

| File | Action | Purpose |
|------|--------|---------|
| `app/services/redis_cache.py` | **Create** | Async Redis cache wrapper — `get`, `set`, `clear`, `close` |
| `app/services/cache.py` | Modify | Add `RedisCache` type hint, cache key helpers |
| `app/services/osrm_client.py` | Modify | `_get()` falls through L1 → L2 → OSRM |
| `app/config.py` | Modify | Add `REDIS_URL`, `REDIS_TTL`, `REDIS_MAXSIZE` settings |
| `app/main.py` | Modify | Initialize and close Redis connection pool in lifespan |
| `pyproject.toml` | Modify | Add `redis[asyncio]` dependency |
| `docker-compose.yml` | Modify | Add `redis` service, `REDIS_URL` env var on `api` |

#### Design: `app/services/redis_cache.py`

```python
import json
import logging
from typing import Any, Dict, Optional
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisCache:
    """Async Redis cache with graceful degradation."""

    def __init__(self, url: str, ttl: int = 900, maxsize: int = 1024):
        self._ttl = ttl
        self._maxsize = maxsize
        self._redis: Optional[redis.Redis] = None
        self._available = False
        if url:
            try:
                self._redis = redis.from_url(url, decode_responses=False)
                self._available = True
            except Exception as e:
                logger.warning("Redis unavailable, falling back to L1-only cache: %s", e)

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self._available or self._redis is None:
            return None
        try:
            data = await self._redis.get(key)
            if data is not None:
                return json.loads(data)
        except Exception as e:
            logger.warning("Redis get failed, falling back: %s", e)
        return None

    async def set(self, key: str, value: Dict[str, Any]) -> None:
        if not self._available or self._redis is None:
            return
        try:
            await self._redis.setex(key, self._ttl, json.dumps(value))
        except Exception as e:
            logger.warning("Redis set failed: %s", e)

    async def clear(self) -> None:
        if not self._available or self._redis is None:
            return
        try:
            await self._redis.flushdb()
        except Exception as e:
            logger.warning("Redis clear failed: %s", e)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._available = False
```

#### Cache-Aside Flow in `OSRMClient._get()`

```
_get(endpoint, params):
    cache_key = build_cache_key(endpoint, params)

    # L1: in-memory
    cached = l1_cache.get(cache_key)
    if cached is not None:
        logger.debug("L1 hit for %s", endpoint)
        return cached

    # L2: Redis
    cached = await redis_cache.get(cache_key)
    if cached is not None:
        logger.debug("L2 hit for %s", endpoint)
        l1_cache[cache_key] = cached      # populate L1
        return cached

    # Miss — fetch from OSRM
    data = await _fetch_with_retry(endpoint, params)

    # Populate both layers
    l1_cache[cache_key] = data             # L1 (sync, sub-ms)
    await redis_cache.set(cache_key, data)  # L2 (async, non-blocking)

    return data
```

#### Redis Configuration (`app/config.py`)

```python
REDIS_URL: str = "redis://localhost:6379/0"
REDIS_TTL: int = 900        # 15 minutes, matches L1
REDIS_MAXSIZE: int = 1024   # LRU eviction hint (advisory)
```

#### Docker Compose Changes

Add to `docker-compose.yml`:

```yaml
redis:
  image: redis:7-alpine
  container_name: osrm-cache
  ports:
    - "6379:6379"
  command: redis-server --save "" --appendonly no
  restart: always

api:
  # ... existing config ...
  environment:
    - OSRM_BASE_URL=http://osrm-backend:5000
    - REDIS_URL=redis://osrm-cache:6379/0
  depends_on:
    - osrm
    - redis
```

#### Dependencies (`pyproject.toml`)

```
redis[asyncio]>=5.2.0
```

#### Verification

1. Start without Redis — `REDIS_URL` empty → cache works in L1-only mode, no errors
2. Start with Redis — make identical `/route` request twice:
   - First request: L1 miss → L2 miss → OSRM fetch (slow, ~200ms)
   - Second request: L1 hit → immediate return (<5ms)
3. Restart the `api` container (simulating deploy):
   - First request: L1 miss → L2 hit → populate L1 → return (fast, ~10ms)
4. `curl` OSRM endpoint directly → bypasses cache (verifies no false caching)

#### Effort: 1.5 days

---

## Dependencies & Ordering

```
Day 1   │ P1: Tracing (app/tracing.py, main.py, pyproject.toml)
        │ Verify: traces exported to OTLP collector
        └─────────────────────────────────────────
Day 2-3 │ P2: Redis Cache (redis_cache.py, osrm_client.py, 
        │     config.py, main.py, docker-compose.yml)
        │ Verify: L1/L2 fallback, cache survives restart
        └─────────────────────────────────────────
Day 4   │ Tests + Documentation
        │ - test_tracing.py: span creation, propagation
        │ - test_redis_cache.py: get/set/clear/fallback
        │ - Update FIT_GAP_ANALYSIS, close GAP-011/012
        └─────────────────────────────────────────
```

**Dependencies:**
- P2 (Redis) depends on P1 (Tracing) only if both modify `main.py` lifespan — minor merge
- Both workstreams are otherwise independent and could run in parallel

---

## Summary

| Gap | Workstream | Effort | Deliverable |
|-----|------------|--------|-------------|
| GAP-011 | Observability | 1 day | `app/tracing.py` — OTel FastAPI + httpx instrumentation, OTLP export |
| GAP-012 | Scalability | 1.5 days | `app/services/redis_cache.py` — async Redis L2 cache, Cache-Aside flow |
| + | Tests + Docs | 0.5 day | `test_tracing.py`, `test_redis_cache.py`, FIT_GAP_ANALYSIS updates |
| **Total** | | **3 days** | **2 new modules + 2 new test files + Docker Compose update** |
