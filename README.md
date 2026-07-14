# OSRM Backend Microservice

[English](https://github.com/lvalverde/osrm-microservice/blob/main/README.md) | [Español](https://github.com/lvalverde/osrm-microservice/blob/main/README.es.md) | [Français](https://github.com/lvalverde/osrm-microservice/blob/main/README.fr.md)

High-performance routing and map-matching microservice for Costa Rica.

### Apple Silicon (M1/M2/M3) Support

Since the official OSRM Docker images are only provided for `linux/amd64`, this project uses Docker's emulation capabilities to run on Apple Silicon.

If you encounter `exec format error` during build, ensure you have emulation enabled. On Docker Desktop for Mac, this is usually automatic. For other environments, you can run:

```bash
docker run --privileged --rm tonistiigi/binfmt --install all
```

The `Makefile` targets (like `make compose-up`) will attempt to run this for you.

## Setup Instructions

This project uses a **Local Build & Bundled Transfer** workflow to support deployment to remote Docker hosts while processing data locally on macOS.

### 1. Prerequisites

- Docker Desktop (macOS)
- Remote Docker Host (e.g., Linux VM at `10.211.55.28`)
- `make`

### 2. Data Acquisition & Local Processing

Extract and process the Costa Rica OSM data locally. This process bundles the data into your local `./data` folder using a Docker-based "No-Mount" builder.

```bash
# Download the latest Costa Rica map data
make download-data

# Process the data locally for a specific profile (car, bicycle, foot)
# Defaults to car if PROFILE is omitted
make process-osrm PROFILE=car
```

### 3. Remote Deployment

Deploy the API and the OSRM engine to the remote host. The processed data is bundled directly from the builder image into the OSRM runtime image via a multi-stage `Dockerfile.osrm`.

`osrm/osrm-backend` supports multiple architectures (amd64, arm64). Confirm your active Docker daemon architecture before starting services.

```bash
# Target the remote host
export DOCKER_HOST=tcp://10.211.55.28:2375

# Check Docker target and architecture
make compose-doctor

# Build and start services with safe sequencing + health checks
# (this command auto-builds `osrm-data-builder` first)
make compose-up

# Tail service logs
make compose-logs

# Stop services
make compose-down
```

Avoid running `docker compose down & docker compose up --build`; `&` backgrounds the first command and can trigger race conditions.

## Core Services

The application encapsulates complex routing logic into several key services located in `app/services/`:

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
# Routing examples
uv run examples/routing/matrix_example.py
uv run examples/routing/route_advanced_options.py
uv run examples/routing/error_handling_demo.py

# VRP examples
uv run examples/vrp/clustering_mode_comparison.py
uv run examples/vrp/hysteresis_demo.py
uv run examples/clustering/simple_id_example.py

# Infrastructure
uv run examples/infra/health_and_metrics.py

# Compare actual vs optimized sequences
uv run examples/benchmarking/compare_tsp.py
```

Maps are saved as interactive HTML files (`map.html`, `comparison_map.html`).

## API Documentation

Interactive documentation is available once the service is running:

- Swagger UI: `http://localhost:8000/docs`
- Redoc: `http://localhost:8000/redoc`

For a detailed developer guide, see:

- [API Reference (English)](docs/API_REFERENCE.md)
- [Referencia de la API (Español)](docs/API_REFERENCE.es.md)
- [Référence API (Français)](docs/API_REFERENCE.fr.md)

## Components

- **OSRM Engine**: C++ routing powerhouse running the MLD algorithm.
- **FastAPI Gateway**: Asynchronous Python API providing specialized endpoints for map matching, graph generation, and Vehicle Routing Problems (VRP).
- **VRP Solver**: Location-Allocation engine for multi-vehicle clustering with support for custom IDs and capacity-based route splitting.
- **NetworkX Integration**: Transparently converts matrix outputs into serializable graphs.
