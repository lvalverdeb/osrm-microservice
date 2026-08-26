# Configuration Reference

All settings are defined by the `settings!` table in `gateway/src/config.rs`,
which declares the name, type and committed default of each in one place. The
reference for their **values** is `deploy/env/app.env`, which both deployment
options load at runtime; tests in that module check the two agree in both
directions, so a setting cannot exist in one and not the other.

## Where settings come from

Three tiers, highest priority last:

| Tier | Source | Docker | FreeBSD jail |
|---|---|---|---|
| 1 | `deploy/env/app.env` — shared, all 29 settings | loaded via `env_file:` in `deploy/docker/docker-compose.yml` | copied to `${JAIL_DIR}/.env` by `deploy/freebsd/install.sh` |
| 2 | deployment overrides | `environment:` in the compose file | overlay block appended to that same `.env` |
| 3 | real process environment | any `-e` / `environment:` entry | anything the rc.d script exports |

Tier 2 beats tier 1 on both paths: Compose documents `environment:` as overriding
`env_file:`, and dotenv takes the **last** occurrence of a duplicated key, which is
why the jail appends its overlay rather than prepending it. Tier 3 beats both,
because the real process environment is read ahead of any env file.

**Only two settings are overridden per deployment** — the rest come from tier 1 in
both:

| Setting | Docker | Jail |
|---|---|---|
| `OSRM_BASE_URL` | `http://osrm-backend:5000` | `JAIL_OSRM_URL`, default `http://127.0.0.1:5000` |
| `REDIS_URL` | `redis://osrm-cache:6379/0` | `JAIL_REDIS_URL`, default `redis://127.0.0.1:6379/0` |

Editing those two in `deploy/env/app.env` therefore has no effect on a deployment.

### What is *not* an app setting

Deployment knobs live with their deployment and never in `deploy/env/app.env`:

- `deploy/docker/.env.example` — `DOCKER_HOST`, `API_PORT`, `OSRM_PORT`, `PROFILE`, `OSM_FILE`
- `deploy/freebsd/.env.example` — `JAIL_HOST`, `JAIL_NAME`, `JAIL_DIR`, `JAIL_API_*`, `GEO_URL`, …
- root `.env.example` — local tooling only (`DOCKER_HOST`, `LOADTEST_*`, `UV_PUBLISH_TOKEN`)

Both `.env.example` files are templates; the file the `Makefile` actually reads is
the gitignored `.env` at the repository root (`-include .env` + `export`).

### Where `.env` actually is

`config.py` sets `env_file=".env"`, a **relative** path resolved against the process
working directory — so its location differs per environment:

| Environment | Path | Notes |
|---|---|---|
| Local dev | repo root | whatever directory you run `uvicorn` from |
| Docker | none | `WORKDIR /app` has no `.env`; settings arrive as process env from compose |
| Jail | `/usr/local/www/osrm-api-gateway/.env` | the rc.d script chdirs there; mode 640, `root:osrmapi` |

A jail with no `.env` starts anyway and logs `no .env in …; falling back to defaults
in app/config.py`.

### Adding a setting

Add the field to the `settings!` table in `gateway/src/config.rs`, then add the key to `deploy/env/app.env` — a
test fails if the two drift apart in either direction. Only touch a deployment file
if the value must differ per deployment.

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

Limits are keyed **per client address, per endpoint** — two clients each get the
full allowance, and `/route` and `/matrix` hold separate buckets.

The address is the immediate TCP peer, so behind a reverse proxy or load balancer
every client collapses into one bucket keyed on the proxy. The deployments fix
that with a trusted-proxy list (`FORWARDED_ALLOW_IPS` / `JAIL_FORWARDED_ALLOW_IPS`)
— see [deployment.md](deployment.md), "Scaling". Plain NAT and L4 forwarding are
unaffected; they preserve the source address.

When `REDIS_URL` is set the counters live in Redis, so limits hold across workers
and nodes. If Redis becomes unreachable the limiter falls back to per-process
in-memory counting rather than failing requests — available, but the effective
limit then multiplies by the number of worker processes.

Per-endpoint request rate limits. Format: `<N>/<unit>` (e.g. `600/minute`), kept from slowapi. An unparseable value stops the process at startup.

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
| `MATRIX_BATCH_SIZE` | `500` | Ceiling on stops per matrix batch sent to OSRM `/table`. The cell budget below binds first once there is more than a handful of depots. |
| `MATRIX_MAX_CELLS` | `10000` | Maximum `sources x destinations` cells in one `/matrix` or `/matrix-graph` request; beyond it the request is rejected with 422. |
| `VRP_CHUNK_CONCURRENCY` | `4` | TSP chunks solved concurrently within one VRP request. Node-wide concurrent `/trip` calls are `WORKERS x VRP_MAX_CONCURRENCY x VRP_CHUNK_CONCURRENCY`. |
| `VRP_HYSTERESIS_M` | `2000.0` | Hysteresis buffer in metres preventing assignment flapping near depot boundaries. |
| `VRP_SANITY_LIMIT_M` | `50000.0` | Maximum Euclidean distance (metres) between a stop's anchor depot and its cost-matrix optimum before the sanity override kicks in. |
| `VRP_MAX_STOPS` | `2000` | Maximum stops accepted in one `/vrp` or `/vrp/allocate` request. Beyond it the request is rejected with 422. |
| `VRP_MAX_CONCURRENCY` | `1` | Solves allowed to run at once **per worker process**. A node admits `WORKERS x VRP_MAX_CONCURRENCY`. |
| `VRP_QUEUE_TIMEOUT` | `10.0` | Seconds a request waits for a free solve slot before it is shed with 503 and a `Retry-After` header. |

`MATRIX_MAX_CELLS` mirrors what `osrm-routed` itself enforces: it refuses a
table request when `sources x destinations` exceeds `--max-table-size` squared,
and an omitted `sources` or `destinations` list means every coordinate. The
default 100 therefore caps a *symmetric* matrix at 100x100 while leaving
asymmetric ones (few sources, many destinations) far larger. Raising this value
means passing `--max-table-size` -- the square root of it -- to `osrm-routed` in
**both** `deploy/docker/docker-compose.yml` and `deploy/freebsd/osrm-routed`, or
the gateway will accept requests the engine then rejects.

`VRP_CHUNK_CONCURRENCY` bounds a different axis: a 2000-stop solve is ~25
`/trip` round trips, and awaiting them one at a time made the request as slow as
their sum. Fanning out at 4 measured 1293 ms -> 364 ms on that shape. Past
roughly twice the engine's core count the calls simply queue at `osrm-routed`
instead of here, so raise it against measured engine latency rather than by
analogy with the worker count.

Peak memory for an optimisation request is stops x concurrent solves: a single
2000-stop solve peaked at 277 MB and four concurrent ones reached 615 MB on a
2 GB host, which is why both factors are bounded. Raise either value only
against a measured RSS ceiling for the target host, and remember
`VRP_MAX_CONCURRENCY` multiplies by the worker count.

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
