# OSRM Backend Microservice

[English](README.md) | [Español](README.es.md) | [Français](README.fr.md)

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

```bash
# Target the remote host
export DOCKER_HOST=tcp://10.211.55.28:2375

# Build and start services
docker compose up -d --build
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
