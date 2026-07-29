# Rate Limiting

All endpoints are protected by per-endpoint rate limits enforced via `slowapi` middleware. Rate limiting is applied per client IP address.

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

Rate limits follow the `slowapi` format: `<N>/<unit>`.

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
| Trip (`/trip`) | 300/min | TSP optimization is compute-intensive for large point sets. |
| VRP (`/vrp`, `/vrp/allocate`) | 100/min | Multi-phase solver: matrix batching + per-cluster TSP. Most expensive endpoint. |
| System (`/health`, `/metrics`) | Unlimited | Monitoring probes must never be blocked. |

---

## Scaling Considerations

`slowapi` uses in-memory rate tracking by default. For distributed deployments with multiple API replicas:

- **Option A:** Place an API gateway (e.g., Nginx, Kong) upstream with global rate limiting.
- **Option B:** Replace `slowapi`'s in-memory store with a Redis-backed store (requires custom `slowapi` storage backend).

Neither is required for single-replica deployments.
