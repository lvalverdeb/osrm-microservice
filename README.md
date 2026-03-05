# OSRM Backend Microservice

High-performance routing and map-matching microservice for Costa Rica.

## Setup Instructions

This project uses a **Local Build & Bundled Transfer** workflow to support deployment to remote Docker hosts while processing data locally on macOS.

### 1. Prerequisites

- Docker Desktop (macOS)
- Remote Docker Host (e.g., Linux VM at `10.211.55.28`)
- `make`

### 2. Data Acquisition & Local Processing

Extract and process the Costa Rica OSM data locally. This process bundles the data into your local `./data` folder using a Docker-based "No-Mount" builder to bypass macOS filesystem restrictions.

```bash
# Download the latest Costa Rica map data
make download-data

# Process the data locally (No volumes used)
make process-osrm
```

### 3. Remote Deployment

Deploy the API and the OSRM engine to the remote host. The processed data is bundled into the OSRM image during the build process and transferred via the Docker build context.

```bash
# Target the remote host
export DOCKER_HOST=tcp://10.211.55.28:2375

# Build and start services
docker compose up -d --build
```

## API Documentation

Once running, interactive documentation is available at:

- Swagger UI: `http://localhost:8000/docs`
- Redoc: `http://localhost:8000/redoc`

## Components

- **OSRM Engine**: C++ routing powerhouse running the MLD algorithm.
- **FastAPI Gateway**: Asynchronous Python API providing specialized endpoints for map matching and graph generation.
- **NetworkX Integration**: Transparently converts matrix outputs into serializable graphs.
