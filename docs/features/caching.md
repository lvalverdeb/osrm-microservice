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

1. **L1 check** — `moka`, in-process, sub-millisecond reads (`gateway/src/main.rs`).
2. **L2 check** — Redis over a pooled, self-reconnecting connection, shared
   across replicas and surviving restarts. On a hit the entry is promoted into
   L1 (`gateway/src/redis_cache.rs`).
3. **OSRM fetch** — on a miss the request reaches OSRM with exponential-backoff
   retry, and the response populates both tiers (`gateway/src/osrm/client.rs`).

Every Redis error is swallowed, so losing L2 costs cache hits and nothing else.
Connect and command timeouts are bounded hard for the same reason: this tier is
an optimisation, and waiting on it longer than a hit is worth defeats the point.

Note the two tiers are not independent. L2 is consulted only after L1 misses, so
its metric series are a subset of L1's misses; summing them for a single hit
rate double-counts.

---

## Cache Key Generation

Keys are built from the endpoint path plus a SHA-256 hash of the sorted query
parameters (`build_cache_key` in `gateway/src/cache.rs`). The hash reproduces
Python's `json.dumps(params, sort_keys=True)` byte for byte — including its
`", "` and `": "` separators, `ensure_ascii`, and float formatting — so keys are
stable across restarts and across replicas sharing one Redis. Cross-language
digests in that module's tests pin the reproduction.

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
