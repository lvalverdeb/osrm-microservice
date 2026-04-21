# OSRM Backend Microservice

[English](https://github.com/lvalverde/osrm-microservice/blob/main/README.md) | [Español](https://github.com/lvalverde/osrm-microservice/blob/main/README.es.md) | [Français](https://github.com/lvalverde/osrm-microservice/blob/main/README.fr.md)

High-performance routing and map-matching microservice for Costa Rica.

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

`ghcr.io/project-osrm/osrm-backend` is currently `amd64` only. Confirm your active Docker daemon architecture before starting services.

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

# 1. Route Plotting
route_payload = {
    "origin": {"lat": 9.9281, "lon": -84.0907},
    "destination": {"lat": 9.9333, "lon": -84.0833},
    "alternatives": True
}
route_res = requests.post(f"{BASE_URL}/route", json=route_payload)

# 2. Traveling Salesperson Problem (TSP)
tsp_payload = {
    "locations": [
        {"lat": 9.9281, "lon": -84.0907},
        {"lat": 9.9333, "lon": -84.0833},
        {"lat": 9.9981, "lon": -84.1107}
    ]
}
tsp_res = requests.post(f"{BASE_URL}/trip", json=tsp_payload)

# 3. Clustering (Location Allocation)
cluster_payload = {
    "depots": [{"id": "D1", "lat": 9.9281, "lon": -84.0907}],
    "locations": [
        {"id": "L1", "lat": 9.9333, "lon": -84.0833},
        {"id": "L2", "lat": 9.9981, "lon": -84.1107}
    ],
    "num_vehicles": 2
}
cluster_res = requests.post(f"{BASE_URL}/vrp/allocate", json=cluster_payload)

# 4. Vehicle Routing Problem (VRP)
vrp_payload = {
    "depots": [{"id": "D1", "lat": 9.9281, "lon": -84.0907}],
    "locations": [
        {"id": "L1", "lat": 9.9333, "lon": -84.0833},
        {"id": "L2", "lat": 9.9981, "lon": -84.1107}
    ],
    "num_vehicles": 2
}
vrp_res = requests.post(f"{BASE_URL}/vrp", json=vrp_payload)
```

## Visualization Tools

The project includes Python tools to visualize and compare routes:

- **`visualize_routes.py`**: Fetches and plots primary and alternate routes for a trip.
- **`compare_tsp.py`**: Compares a provided sequence of stops (Actual) against a TSP-optimized round-trip (Optimized).
- **`matrix_example.py`**: Demonstrates how to generate a distance and duration table (matrix) between multiple origins and destinations.
- **`simple_id_example.py`**: A comprehensive VRP demonstrator that simulates 10 vehicles across multiple depots and generates an interactive Folium map.

**Usage**:

```bash
# Run the distance/duration matrix example
uv run examples/routing/matrix_example.py

# Run the 10-vehicle VRP simulation
uv run examples/clustering/simple_id_example.py

# Compare actual vs optimized sequences
uv run compare_tsp.py
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
