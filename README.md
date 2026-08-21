# OSRM Backend Microservice

[English](https://github.com/lvalverde/osrm-microservice/blob/main/README.md) | [Español](https://github.com/lvalverde/osrm-microservice/blob/main/README.es.md) | [Français](https://github.com/lvalverde/osrm-microservice/blob/main/README.fr.md)

High-performance routing and map-matching microservice for Costa Rica.

## Deployment

The project supports **two** deployment options. Both run the same three services
— the OSRM engine, a Redis cache and the FastAPI gateway — and everything either
one needs lives under [`deploy/`](deploy).

| Option | Files | Start with | When |
|---|---|---|---|
| **Docker** | [`deploy/docker/`](deploy/docker) | `make compose-up` | Any Linux Docker host, local or remote |
| **FreeBSD jail** | [`deploy/freebsd/`](deploy/freebsd) | `make jail-up` | A jail on a FreeBSD host, which cannot run Docker |

Full instructions for both, including prerequisites and Apple Silicon notes, are
in **[docs/deployment.md](docs/deployment.md)**.

### Docker, in short

Data is processed into an image on your machine and bundled into the runtime
image by the multi-stage `deploy/docker/Dockerfile.osrm`, so nothing is
bind-mounted and the stack can be deployed to a remote Docker host as-is.

```bash
make download-data              # fetch the Costa Rica extract into ./data
make process-osrm PROFILE=car   # extract / partition / customize

export DOCKER_HOST=tcp://10.211.55.28:2375   # optional: target a remote daemon
make compose-doctor             # show the active Docker host and architecture
make compose-up                 # build and start, with sequencing and health checks
make compose-logs
make compose-down
```

Avoid `docker compose down & docker compose up --build`; the `&` backgrounds the
first command and the two race.

### FreeBSD jail, in short

A jail cannot run Docker — jails share the FreeBSD kernel, Docker needs Linux
namespaces and cgroups — so the same services run natively from packages and rc.d
scripts. See [docs/deployment_freebsd.md](docs/deployment_freebsd.md).

```bash
make jail-doctor      # check the target and how to escalate
make jail-bootstrap   # packages and service user
make jail-data        # build OSRM data in the jail
make jail-up          # deploy the gateway and start all services
```

## Core Services

The application encapsulates complex routing logic into several key services located in `src/app/services/`:

### 1. OSRM Client (`osrm_client.py`)
An asynchronous HTTP client that interacts directly with the C++ OSRM backend. It formats queries and standardizes responses.
**Example Use Case**: Fetching the exact geometry and driving instructions for a trip between a warehouse and multiple delivery points.

### 2. Graph Builder (`graph_builder.py`)
Transforms raw OSRM distance and duration matrices into directed `NetworkX` graphs.
**Example Use Case**: Generating a mathematical representation of the road network to feed into advanced optimization algorithms (like custom TSP solvers) or to identify isolated nodes in the delivery network.

### 3. VRP Service (`vrp_service.py`)
A comprehensive Vehicle Routing Problem (VRP) solver. It implements a Location-Allocation strategy, assigning delivery stops to the nearest available warehouse (depot) and generating optimized delivery sequences.
**Example Use Case**: A logistics company wants to distribute 500 daily packages across 5 drivers starting from 2 different warehouses, ensuring each driver takes the most optimal cluster of stops.

## Client Application Usage Examples

Here are some examples of how a client application can interact with the FastAPI microservice using Python's `requests` library:

```python
import requests

BASE_URL = "http://localhost:8000"

# 1. Route Plotting (Walking Profile)
route_payload = {
    "origin": {"longitude": -84.0907, "latitude": 9.9281},
    "destination": {"longitude": -84.0833, "latitude": 9.9333},
    "profile": "walking",
    "steps": True
}
route_res = requests.post(f"{BASE_URL}/route", json=route_payload)

# 2. Nearest Point (Road Snapping)
nearest_payload = {
    "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
    "number": 3
}
nearest_res = requests.post(f"{BASE_URL}/nearest", json=nearest_payload)

# 3. Traveling Salesperson Problem (TSP)
tsp_payload = {
    "coordinates": [
        {"longitude": -84.0907, "latitude": 9.9281},
        {"longitude": -84.0833, "latitude": 9.9333},
        {"longitude": -84.1107, "latitude": 9.9981}
    ]
}
tsp_res = requests.post(f"{BASE_URL}/trip", json=tsp_payload)

# 4. Clustering (Location Allocation)
cluster_payload = {
    "depots": [{"id": "D1", "longitude": -84.0907, "latitude": 9.9281}],
    "stops": [
        {"id": "L1", "longitude": -84.0833, "latitude": 9.9333},
        {"id": "L2", "longitude": -84.1107, "latitude": 9.9981}
    ],
    "vehicle_count": 2
}
cluster_res = requests.post(f"{BASE_URL}/vrp/allocate", json=cluster_payload)

# 5. Vehicle Routing Problem (VRP)
vrp_payload = {
    "depots": [{"id": "D1", "longitude": -84.0907, "latitude": 9.9281}],
    "stops": [
        {"id": "L1", "longitude": -84.0833, "latitude": 9.9333},
        {"id": "L2", "longitude": -84.1107, "latitude": 9.9981}
    ],
    "vehicle_count": 2
}
vrp_res = requests.post(f"{BASE_URL}/vrp", json=vrp_payload)
```

## Visualization Tools

The project includes Python tools to visualize and compare routes:

### Example Scripts

| Category | Script | What It Demonstrates |
|----------|--------|---------------------|
| **Routing** | `visualize_routes.py` | Primary + alternate routes with distance/duration popups |
| | `route_advanced_options.py` | Bearing constraints, road exclusion, continue_straight, step annotations |
| | `error_handling_demo.py` | 8 error scenarios: 422, 429, connection errors, validation |
| | `matrix_example.py` | Distance/duration matrix table between multiple cities |
| | `matrix_graph_example.py` | Matrix-to-graph conversion with node/edge attributes |
| | `nearest_example.py` | Road snapping with multiple nearest segments |
| | `match_example.py` | GPS trace map matching with raw vs matched geometry |
| | `tile_example.py` | Mapbox Vector Tile download from `/tile` |
| **Benchmarking** | `compare_tsp.py` | Actual vs TSP-optimized delivery sequence comparison |
| | `clustering_mode_comparison.py` | travel_time vs distance vs radial clustering on same dataset |
| | `hysteresis_demo.py` | Hysteresis buffer preventing assignment flapping |
| **VRP** | `visualize_vrp.py` | Multi-warehouse VRP with color-coded vehicle routes |
| | `stress_test_vrp.py` | 6 warehouses + 2500 stops stress test |
| | `simple_id_example.py` | 10 vehicles, 300 stops with custom IDs |
| | `run_clustering_workflow.py` | 6500-stop clustering with road distance vs travel time |
| **Infrastructure** | `health_and_metrics.py` | Health probe, Prometheus metrics, caching, retry, logging |

**Usage**:

```bash
# Or launch the interactive menu (discovers all scripts automatically)
uv run examples/main.py

# Routing examples
uv run examples/src/routing/matrix_example.py
uv run examples/src/routing/route_advanced_options.py
uv run examples/src/routing/error_handling_demo.py

# VRP examples
uv run examples/src/vrp/clustering_mode_comparison.py
uv run examples/src/vrp/hysteresis_demo.py
uv run examples/src/clustering/simple_id_example.py

# Infrastructure
uv run examples/src/infra/health_and_metrics.py

# Compare actual vs optimized sequences
uv run examples/src/benchmarking/compare_tsp.py
```

Maps are saved as interactive HTML files (`map.html`, `comparison_map.html`).

## Load Testing

`loadtest/run.py` drives a running gateway at a fixed **arrival rate** — requests
are launched on a schedule rather than after the previous one returns, so a slow
server shows up as rising latency instead of a quietly lower rate. Payloads are
randomised per request, which also keeps the L1/Redis caches from answering
everything. It uses `httpx`, already a project dependency; nothing to install.

Every endpoint has a scenario — `health`, `metrics`, `route`, `nearest`,
`matrix`, `matrix-graph`, `trip`, `match`, `tile`, `vrp`, `vrp-allocate` — and
the default `mixed` fires a weighted blend of all of them concurrently, which is
what a real client population looks like. Mixed runs print a per-endpoint
breakdown.

```sh
make loadtest                                              # mixed @ 25/s for 30s, localhost
make loadtest LOADTEST_URL=http://10.211.55.33:8000        # against a deployed jail
make loadtest LOADTEST_SCENARIO=matrix LOADTEST_RATE=5
make loadtest LOADTEST_SCENARIO=vrp LOADTEST_RATE=1 LOADTEST_ARGS="--size 500"
```

```
mixed @ 30.0/s for 25.0s -> http://10.211.55.33:8000
  requests   751 in 25.1s (29.9/s completed)
  latency    p50=53ms p95=91ms p99=177ms max=366ms
  endpoint                 n     p50     p95     p99   err%  statuses
  /route                 272     53m     85m     89m   0.0%  200=272
  /nearest               123     53m     86m    145m   0.0%  200=123
  /vrp                    18    150m    177m    366m   0.0%  200=18
  ...
```

| Variable | Default | Meaning |
|---|---|---|
| `LOADTEST_URL` | `http://127.0.0.1:8000` | Target gateway |
| `LOADTEST_SCENARIO` | `mixed` | Any single endpoint, or `mixed` for the blend |
| `LOADTEST_RATE` | `25` | Requests launched per second |
| `LOADTEST_DURATION` | `30` | Seconds |
| `LOADTEST_ARGS` | — | Passed through, e.g. `--size 500 --seed 42` |

Thresholds turn a run into a pass/fail gate — the target exits non-zero when one
is exceeded:

```sh
make loadtest LOADTEST_ARGS="--max-p95 0.5 --max-error-rate 0.01"
```

Four things to keep in mind when reading the numbers:

- **Rate limits are per client IP** (`RATE_LIMIT_ROUTE` 600/min, `RATE_LIMIT_VRP`
  100/min) and use fixed windows, so a short run can straddle two windows and
  never trip. Measured: 100/s for 8s → 547×200 and 254×429. Sustained runs above
  the limit measure the rate limiter, not capacity — a single-source run cannot
  exceed one bucket. Use `--forwarded-for-pool N` against a gateway configured
  with a matching trusted-proxy list to measure the service instead.
  Behind a reverse proxy the client IP is the *proxy's*, so all clients share one
  bucket until the deployment names its proxies — see
  [docs/deployment.md](docs/deployment.md), "Scaling".
- **`/matrix` is capped at 100 coordinates** by `osrm-routed`'s
  `--max-table-size` default, whatever the schema allows. The generator clamps
  to that.
- **One uvicorn worker by default**, on both deployments. VRP work is CPU-bound,
  so concurrency is not parallelism; raise `API_WORKERS` (Docker) or
  `JAIL_API_WORKERS` (jail) before drawing conclusions.
- **Memory scales with payload size, not request count.** Measured on a 2 GB
  jail: flat RSS across 400 sequential `/route` calls, but roughly +70 MB per
  1000 VRP stops in flight. Bound the jail with
  `rctl -a jail:api:memoryuse:deny=1g` before pushing a shared host hard.

## Configuration

All settings are controlled via environment variables (or a `.env` file). See the [Configuration Reference](docs/configuration.md) for the complete list.

## API Documentation

Interactive documentation is available once the service is running:

- Swagger UI: `http://localhost:8000/docs`
- Redoc: `http://localhost:8000/redoc`

For a detailed developer guide, see:

- [API Reference (English)](docs/API_REFERENCE.md)
- [Referencia de la API (Español)](docs/API_REFERENCE.es.md)
- [Référence API (Français)](docs/API_REFERENCE.fr.md)

## Feature Documentation

| Feature | Description |
|---------|-------------|
| [Response Caching](docs/features/caching.md) | L1/L2 Cache-Aside strategy with in-memory and Redis layers. |
| [VRP Clustering Modes](docs/features/clustering_modes.md) | `travel_time`, `distance`, and `radial` allocation with hysteresis. |
| [Observability](docs/features/observability.md) | Structured logging, Prometheus metrics, OpenTelemetry tracing, health checks. |
| [Rate Limiting](docs/features/rate_limiting.md) | Per-endpoint request limits and configuration. |

## Components

- **OSRM Engine**: C++ routing powerhouse running the MLD algorithm.
- **FastAPI Gateway**: Asynchronous Python API providing specialized endpoints for map matching, graph generation, and Vehicle Routing Problems (VRP).
- **VRP Solver**: Location-Allocation engine for multi-vehicle clustering with support for custom IDs and capacity-based route splitting.
- **NetworkX Integration**: Transparently converts matrix outputs into serializable graphs.
