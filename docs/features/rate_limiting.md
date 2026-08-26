# Rate Limiting

Every routing endpoint carries a per-endpoint limit, counted per client IP
(`gateway/src/ratelimit.rs`). `/health`, `/ready` and `/metrics` are never
limited, so a probe or a scrape is never shed.

---

## Default Limits

| Endpoint | Limit | Environment Variable |
|----------|-------|---------------------|
| `POST /route` | 600/min | `RATE_LIMIT_ROUTE` |
| `POST /matrix` | 300/min | `RATE_LIMIT_MATRIX` |
| `POST /matrix-graph` | 300/min | `RATE_LIMIT_MATRIX` |
| `POST /match` | 600/min | `RATE_LIMIT_MATCH` |
| `POST /trip` | 300/min | `RATE_LIMIT_TRIP` |
| `POST /nearest` | 600/min | `RATE_LIMIT_NEAREST` |
| `GET /tile/{p}/{z}/{x}/{y}.mvt` | 600/min | `RATE_LIMIT_TILE` |
| `POST /vrp` | 100/min | `RATE_LIMIT_VRP` |
| `POST /vrp/allocate` | 100/min | `RATE_LIMIT_VRP` |
| `GET /health` | Unlimited | — |
| `GET /metrics` | Unlimited | — |

---

## Format

Rate limits keep slowapi's spelling: `<N>/<unit>`. A value that cannot be parsed
stops the process at startup, naming the setting — an endpoint that comes up
silently unlimited is the one failure a rate limiter must not have.

Supported units: `second`, `minute`, `hour`, `day`.

Examples:
- `600/minute` — 600 requests per minute.
- `10/second` — 10 requests per second (600/minute equivalent).
- `5000/hour` — 5000 requests per hour.

---

## Configuration

Override any limit via environment variables (or `.env` file):

```bash
# Increase VRP limit for internal batch jobs
RATE_LIMIT_VRP=500/minute

# Relax matrix limit for dashboard use
RATE_LIMIT_MATRIX=1000/minute
```

---

## Error Response

When a rate limit is exceeded, the gateway returns:

```json
{
  "detail": "Rate limit exceeded: 600 per minute"
}
```

HTTP Status: `429 Too Many Requests`.

---

## Design Rationale

| Endpoint Category | Limit | Rationale |
|-------------------|-------|-----------|
| Routing (`/route`, `/match`, `/nearest`, `/tile`) | 600/min | Lightweight OSRM pass-through, high throughput expected. |
| Matrix (`/matrix`, `/matrix-graph`) | 300/min | Matrix computation is heavier; OSRM `/table` has internal size limits. |
| Trip (`/trip`) | 300/min | TSP optimisation is compute-intensive for large point sets. |
| VRP (`/vrp`, `/vrp/allocate`) | 100/min | Multi-phase solver: matrix batching + per-cluster TSP. Most expensive endpoint. |
| System (`/health`, `/metrics`) | Unlimited | Monitoring probes must never be blocked. |

---

## Scaling Considerations

Counting in process memory alone makes the configured limit per-instance, so a
fleet of M instances behind a balancer allows M times what the setting says.
That is why Redis-backed counting is not optional here — see Storage below. An
upstream gateway (Nginx, Kong) with a global limit remains a reasonable second
layer, but it is not a substitute.

## Storage

Limits are counted in Redis when `REDIS_URL` is set, and in process memory
otherwise, falling back automatically when Redis does not answer — an outage
degrades to per-instance counting rather than stopping enforcement.

`WORKERS` is tokio worker threads inside one process, so the in-process counter
is already global to an instance. What it cannot see is a second instance: a
fleet of M behind a balancer allows M times the configured value unless the
counting is shared.

Measured on the jail deployment, 800 requests in 8s against `RATE_LIMIT_ROUTE`
of 600/minute:

| Setup | Allowed | Throttled |
|---|---|---|
| 1 worker | 547 | 254 |
| 2 workers, in-memory storage | 772 | 29 |
| 2 workers, Redis storage | 549 | 252 |

`in_memory_fallback_enabled` keeps the gateway serving if Redis goes away — the
limits revert to per-process until it returns, which is the same
degrade-rather-than-fail stance `RedisCache` takes.
