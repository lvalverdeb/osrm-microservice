# Response Caching

The gateway uses a two-level **Cache-Aside** strategy to avoid redundant OSRM calls for identical route and matrix requests.

---

## Architecture

```
Client Request
      │
      ▼
┌─────────────┐  hit   ┌──────────────┐  hit   ┌──────────┐
│  L1: In-    │ ─────> │  L2: Redis   │ ─────> │  OSRM    │
│  Memory     │        │  (shared)    │        │  Backend │
│  TTLCache   │        │  TTL 900s    │        │          │
└──────┬──────┘        └──────┬───────┘        └────┬─────┘
       │ miss                 │ miss                │
       │                      │                     │
       └──────────────────────┴─────────────────────┘
              response flows back, populating each layer
```

**Flow:**

1. **L1 check** — `cachetools.TTLCache` (in-process, sub-millisecond reads).
2. **L2 check** — `redis.asyncio.Redis` (shared across replicas, survives restarts). On hit, the entry is promoted into L1.
3. **OSRM fetch** — On miss, the request hits OSRM with exponential-backoff retry (`tenacity`). The response populates both L2 and L1.

Both layers are populated synchronously for L1 and asynchronously (non-blocking `await`) for L2, so a Redis failure never delays the response.

---

## Cache Key Generation

Keys are built from the endpoint path plus a SHA-256 hash of the sorted query parameters (`build_cache_key` in `app/services/cache.py`). The hash uses `json.dumps` with `sort_keys=True`, making keys stable across process restarts and replicas sharing the same Redis instance.

```
/route/v1/driving/<sha256_of_params>
/table/v1/driving/<sha256_of_params>
```

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `L1_CACHE_TTL` | `900` | In-memory entry TTL (seconds). |
| `L1_CACHE_MAXSIZE` | `1024` | Max entries in the LRU cache. |
| `REDIS_URL` | *(empty)* | Redis connection URL. Empty disables L2. |
| `REDIS_TTL` | `900` | Redis entry TTL (seconds). |
| `REDIS_MAXSIZE` | `1024` | Advisory size hint for Redis. |

See [Configuration Reference](../configuration.md) for full details.

---

## Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| `REDIS_URL` empty | L1-only mode. No errors, no L2 calls. |
| Redis unreachable at startup | L1-only mode. Redis is marked unavailable. |
| Redis fails mid-request | Warning logged. L1 still serves cached entries. |
| OSRM unreachable | Retry exhausts, 500 returned. Cache is not populated. |

---

## Cacheable Endpoints

All `_get()`-based endpoints are cached:

| Endpoint | Cacheable | Notes |
|----------|-----------|-------|
| `POST /route` | Yes | |
| `POST /matrix` | Yes | |
| `POST /matrix-graph` | Yes | Built from cached matrix response. |
| `POST /match` | Yes | |
| `POST /trip` | Yes | |
| `POST /nearest` | Yes | |
| `GET /tile/...` | **No** | Binary responses bypass `_get()`. |
| `POST /vrp` | **No** | VRP calls `_get()` internally for matrix/trip, but the final VRP response is not cached. |
| `GET /health` | **No** | Probes OSRM every time. |

---

## Tuning Recommendations

- **High-traffic, single-deploy**: Increase `L1_CACHE_MAXSIZE` to reduce Redis round-trips.
- **Multi-replica horizontal scaling**: Keep `REDIS_URL` set. L1 is per-process; L2 is shared.
- **Frequently changing road data**: Reduce `REDIS_TTL` to avoid stale durations/distances.
- **Memory-constrained containers**: Reduce `L1_CACHE_MAXSIZE` or `REDIS_MAXSIZE`.
