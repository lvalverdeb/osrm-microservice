# Observability

The gateway provides three complementary observability layers: **structured logging**, **Prometheus metrics**, and **OpenTelemetry distributed tracing**.

---

## Structured Logging

Configured in `app/logging_config.py`, called at startup before any other module is imported.

**Format:**
```
2026-06-25 14:32:01,123 [INFO] app.main: POST /route completed in 0.142s
```

**Configuration:**

| Variable | Default | Effect |
|----------|---------|--------|
| `DEBUG` | `false` | `true` sets log level to `DEBUG`. |
| `APPEND_TO_STDERR` | `false` | `true` routes logs to `stderr` (useful for Docker log drivers). |

**Log levels by component:**

| Component | Level | Content |
|-----------|-------|---------|
| `app.main` | `INFO` / `ERROR` | Request completion, OSRM errors, unexpected exceptions. |
| `app.services.osrm_client` | `DEBUG` / `ERROR` | L1/L2 cache hits, OSRM API errors, retry attempts. |
| `app.services.redis_cache` | `WARNING` / `INFO` | Redis connection status, get/set failures. |
| `app.tracing` | `INFO` / `WARNING` | Tracing enable/disable, exporter failures. |

---

## Prometheus Metrics

Endpoint: `GET /metrics` (configurable via `METRICS_ENDPOINT`).

Auto-instrumented by `prometheus-fastapi-instrumentator`, which tracks per-route:

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | Total requests per endpoint, method, status code. |
| `http_request_duration_seconds` | Histogram | Request latency distribution. |
| `http_request_size_bytes` | Histogram | Request body size. |
| `http_response_size_bytes` | Histogram | Response body size. |

### Cache metrics

The gateway also exports its own counter for the two-tier cache-aside path in
`OSRMClient._get`:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `cache_lookups_total` | Counter | `tier`, `result`, `service` | One increment per cache lookup. |

- `tier` — `l1` (in-process `TTLCache`) or `l2` (Redis).
- `result` — `hit` or `miss`.
- `service` — the OSRM service: `route`, `table`, `match`, `trip`, `nearest`, or
  `other`. The raw endpoint carries coordinates, so it can never be a label —
  every distinct request would mint a new time series. Tiles are not cached and
  never appear.

Two properties matter when reading it:

**L2 is nested inside L1's misses.** Redis is consulted only after an L1 miss, so
the `l2` series are a subset of `l1{result="miss"}`. Adding the tiers together
double-counts; compute each tier's rate against its own total.

**An unconfigured tier records nothing.** With no `REDIS_URL`, no `l2` series
appear at all — an absent tier is not a miss. An `l2` series that is present but
flat means Redis is reachable and simply never answering.

```promql
# L1 hit rate
sum(rate(cache_lookups_total{tier="l1",result="hit"}[5m]))
  / sum(rate(cache_lookups_total{tier="l1"}[5m]))

# L2 hit rate -- denominator is L2 lookups, not all lookups
sum(rate(cache_lookups_total{tier="l2",result="hit"}[5m]))
  / sum(rate(cache_lookups_total{tier="l2"}[5m]))

# Which services actually benefit from caching
sum by (service) (rate(cache_lookups_total{result="hit"}[5m]))
```

A zero L2 hit rate under `make loadtest` is expected rather than a fault: the
generator randomises payloads so keys rarely repeat, and with a single uvicorn
worker the L1 tier absorbs the repeats that do occur before Redis is consulted.
See [../deployment.md](../deployment.md) for load-testing either deployment.

**Scraping configuration** (Prometheus):
```yaml
scrape_configs:
  - job_name: 'osrm-api-gateway'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: '/metrics'
```

**Grafana dashboard fields:**
- **Request rate**: `rate(http_requests_total[5m])`
- **Error rate**: `rate(http_requests_total{status=~"5.."}[5m])`
- **p95 latency**: `histogram_quantile(0.95, http_request_duration_seconds_bucket)`

---

## OpenTelemetry Distributed Tracing

When `OTLP_ENDPOINT` is set, the gateway exports request spans to an OTLP-compatible collector (e.g., Jaeger, Tempo, Grafana Cloud).

**Architecture:**

```
Client ──> FastAPI Gateway ──> OSRM Backend
            │                    │
            ├── Root Span        │
            │   POST /vrp        │
            │                    │
            ├── Child Span       │
            │   GET /table/v1/.. │ ──────> OSRM
            │                    │
            ├── Child Span       │
            │   GET /trip/v1/..  │ ──────> OSRM
            │                    │
            └── Child Span       │
                GET /trip/v1/..  │ ──────> OSRM
```

**Auto-instrumentation:**
- `FastAPIInstrumentor` creates a root span per inbound request (method, path, status).
- `HTTPXClientInstrumentor` creates child spans for each outbound OSRM call.
- W3C `traceparent` headers propagate trace context across service boundaries.

**Configuration:**

| Variable | Default | Effect |
|----------|---------|--------|
| `OTLP_ENDPOINT` | *(empty)* | Leave empty to disable tracing. Set to `http://localhost:4318/v1/traces` for a local collector. |

**Behavior when disabled:** The tracing middleware is installed but spans are dropped silently — no errors, no performance impact.

---

## Health and Readiness

Two endpoints, two audiences: `GET /health` describes the state for humans and
dashboards and always answers 200; `GET /ready` is the machine-readable verdict
and answers 503 when the node cannot serve traffic. Both probe the OSRM
backend the same way.

### `GET /health`

The health check actively probes the OSRM backend to report true system health.

**Response:**
```json
{
  "status": "healthy",
  "service": "OSRM API Gateway",
  "osrm_backend": "up"
}
```

**Degraded state** (OSRM unreachable):
```json
{
  "status": "degraded",
  "service": "OSRM API Gateway",
  "osrm_backend": "down"
}
```

### `GET /ready`

Returns 200 with `{"status": "ready", ...}` while the OSRM backend answers, and
**503** with `{"status": "not_ready", "osrm_backend": "down"}` when it does not.

Point load balancers and deploy-time checks here rather than at `/health`: a
balancer reading `/health` sees 200 even in the degraded state and keeps sending
traffic to a node that cannot route. `make compose-health` and `make jail-health`
already use `/ready`. Like `/health` and `/metrics`, it is not rate-limited.

**Configuration:**

| Variable | Default | Effect |
|----------|---------|--------|
| `HEALTH_CHECK_TIMEOUT` | `2` | Seconds before the OSRM probe times out. Must stay below Docker's `HEALTHCHECK --timeout` (8 s). |
| `HEALTH_CHECK_COORDS` | `0,0;0,0` | Coordinate pair used for the probe route. |

**Docker Compose health check** (in `deploy/docker/docker-compose.yml`):
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/ready"]
  interval: 30s
  timeout: 8s
  retries: 3
```

---

## Integration

| Layer | Enabled By Default | Requires |
|-------|-------------------|----------|
| Structured logging | Yes | Nothing |
| Prometheus metrics | Yes | Nothing |
| Health check | Yes | OSRM backend (for full probe) |
| OpenTelemetry tracing | No | `OTLP_ENDPOINT` set to a collector URL |
