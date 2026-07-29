# Configuration Reference

All settings are loaded from environment variables (or a `.env` file at the project root) via Pydantic `BaseSettings`. A reference `.env.example` is provided in the repository root.

---

## Core Application

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `OSRM API Gateway` | Application name shown in logs and `/health` response. |
| `DEBUG` | `false` | When `true`, sets log level to `DEBUG`. |
| `OSRM_BASE_URL` | `http://localhost:5000` | Internal URL of the OSRM C++ backend (used by the gateway). |
| `OSRM_API_URL` | `http://localhost:8080` | Public URL of the FastAPI gateway (used by examples/clients). |

---

## OSRM Client / Retry

| Variable | Default | Description |
|----------|---------|-------------|
| `OSRM_CLIENT_TIMEOUT` | `30` | Timeout (seconds) for each outbound HTTP request to OSRM. |
| `OSRM_RETRY_ATTEMPTS` | `3` | Maximum retry attempts per failed OSRM call. |
| `OSRM_RETRY_MIN` | `1` | Minimum wait (seconds) before the first retry. |
| `OSRM_RETRY_MAX` | `10` | Maximum wait (seconds) between retries. |

Retries apply only to 5xx errors, timeouts, and transport errors. 4xx errors are never retried.

---

## Rate Limiting

Per-endpoint request rate limits enforced by `slowapi`. Format: `<N>/<unit>` (e.g. `600/minute`).

| Variable | Default | Applies To |
|----------|---------|------------|
| `RATE_LIMIT_ROUTE` | `600/minute` | `POST /route` |
| `RATE_LIMIT_MATRIX` | `300/minute` | `POST /matrix`, `POST /matrix-graph` |
| `RATE_LIMIT_MATCH` | `600/minute` | `POST /match` |
| `RATE_LIMIT_TRIP` | `300/minute` | `POST /trip` |
| `RATE_LIMIT_NEAREST` | `600/minute` | `POST /nearest` |
| `RATE_LIMIT_TILE` | `600/minute` | `GET /tile/{p}/{z}/{x}/{y}.mvt` |
| `RATE_LIMIT_VRP` | `100/minute` | `POST /vrp`, `POST /vrp/allocate` |

`GET /health` and `GET /metrics` are unlimited.

---

## Caching

### L1: In-Memory

| Variable | Default | Description |
|----------|---------|-------------|
| `L1_CACHE_TTL` | `900` | TTL in seconds for in-memory cache entries (default 15 min). |
| `L1_CACHE_MAXSIZE` | `1024` | Maximum number of entries in the LRU in-memory cache. |

### L2: Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | *(empty)* | Redis connection URL. Leave empty to disable L2 (L1-only mode). |
| `REDIS_TTL` | `900` | TTL in seconds for Redis cache entries (default 15 min). |
| `REDIS_MAXSIZE` | `1024` | Advisory max size hint (Redis itself does not enforce this). |

When `REDIS_URL` is empty or Redis is unreachable, the gateway falls back to L1-only mode without errors.

---

## OpenTelemetry Tracing

| Variable | Default | Description |
|----------|---------|-------------|
| `OTLP_ENDPOINT` | *(empty)* | OTLP HTTP endpoint for span export. Leave empty to disable tracing. |

When disabled, the tracing middleware is still installed but spans are dropped silently.

---

## Health Check

| Variable | Default | Description |
|----------|---------|-------------|
| `HEALTH_CHECK_TIMEOUT` | `2` | Timeout (seconds) for the OSRM probe inside `GET /health`. Must stay well below the Docker `HEALTHCHECK` timeout (8 s). |
| `HEALTH_CHECK_COORDS` | `0,0;0,0` | Coordinate pair used by the health probe (`/route/v1/driving/{coords}`). |

---

## VRP / Matrix Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `VRP_CHUNK_SIZE` | `80` | Maximum stops per TSP chunk sent to OSRM `/trip` (OSRM's hard limit is 200). |
| `MATRIX_BATCH_SIZE` | `500` | Number of stops per matrix batch sent to OSRM `/table`. |
| `VRP_HYSTERESIS_M` | `2000.0` | Hysteresis buffer in meters preventing assignment flapping near depot boundaries. |
| `VRP_SANITY_LIMIT_M` | `50000.0` | Maximum Euclidean distance (meters) between a stop's anchor depot and its cost-matrix optimum before the sanity override kicks in. |

---

## Metrics

| Variable | Default | Description |
|----------|---------|-------------|
| `METRICS_ENDPOINT` | `/metrics` | Path where Prometheus metrics are exposed. |

---

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `APPEND_TO_STDERR` | `false` | When `true`, log output goes to `stderr` instead of `stdout`. Useful for container log aggregation drivers that separate stdout/stderr. |
