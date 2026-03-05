Agentic Code Assistant Instructions: OSRM Backend Microservice

1. Context & Architecture Goal
You are an expert backend software engineer and data engineer. Your task is to build a high-performance routing and map-matching microservice.

The architecture consists of two main components:

OSRM Engine (C++): A standalone Dockerized OSRM instance loaded with the latest OpenStreetMap data for Costa Rica. It will use the Multi-Level Dijkstra (MLD) pipeline for fast compilation, flexible routing, and GPS trace matching.

API Gateway (Python): An asynchronous REST API that proxies requests to OSRM and provides specialized endpoints, including graph matrix generation and GPS breadcrumb map matching.

Required Tech Stack:

Infrastructure: Docker, Docker Compose

Backend: Python 3.12+, FastAPI, Pydantic

Package Management: uv

Libraries: httpx (for async requests to OSRM), networkx (for graph modeling)

1. Directory Structure
Please generate the project using the following strict directory structure:

osrm-microservice/
├── docker-compose.yml
├── Makefile
├── data/                  # Directory for OSM data (gitignored)
└── app/
    ├── pyproject.toml     # Managed by uv
    ├── main.py            # FastAPI application entry point
    ├── config.py          # Pydantic BaseSettings
    ├── services/
    │   ├── osrm_client.py # Async httpx client for OSRM
    │   └── graph_builder.py # NetworkX integration logic
    └── models/
        └── schemas.py     # Pydantic request/response models

1. Step-by-Step Implementation Instructions
Step 1: Map Data Acquisition & Initialization (Makefile)
Write a Makefile to handle downloading the data and running the OSRM preprocessing pipeline.

Target 1 (download-data): Use wget or curl to download costa-rica-latest.osm.pbf from the Geofabrik server (<http://download.geofabrik.de/central-america/costa-rica-latest.osm.pbf>) into the data/ directory.

Target 2 (process-osrm): Write the Docker commands to run the OSRM MLD pipeline on the downloaded Costa Rica file using the ghcr.io/project-osrm/osrm-backend image. The sequence must be:

docker run -v $(PWD)/data:/data ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/costa-rica-latest.osm.pbf

docker run -v $(PWD)/data:/data ghcr.io/project-osrm/osrm-backend osrm-partition /data/costa-rica-latest.osrm

docker run -v $(PWD)/data:/data ghcr.io/project-osrm/osrm-backend osrm-customize /data/costa-rica-latest.osrm

Step 2: Docker Compose Configuration (docker-compose.yml)
Create a docker-compose.yml file to spin up the infrastructure.

Service 1 (osrm): Run the osrm-backend image, expose port 5000, mount the data/ volume, and set the command to run osrm-routed --algorithm mld /data/costa-rica-latest.osrm.

Service 2 (api): Build the FastAPI application from the ./app directory, expose port 8000, and set OSRM_BASE_URL=<http://osrm:5000> as an environment variable.

Step 3: Package Management (pyproject.toml)
Generate the pyproject.toml utilizing uv for dependency management. Include fastapi, uvicorn, pydantic-settings, httpx, and networkx.

Step 4: Data Models (app/models/schemas.py)
Define Pydantic schemas for the incoming requests.

Define standard coordinate inputs for routing and matrix endpoints.

Define models for Map Matching. This requires a GPSBreadcrumb model with longitude, latitude, timestamp (integer), and accuracy_meters (optional float, default 5.0).

Define a MatchRequest model containing a list of GPSBreadcrumb objects.

Step 5: FastAPI Application (app/main.py)
Implement the FastAPI server with the following endpoints:

Endpoint 1 (/route): Accepts origin and destination coordinates, uses httpx to call the OSRM /route/v1/driving/ endpoint, and returns the geometry and travel time.

Endpoint 2 (/matrix-graph): Accepts a payload of multiple coordinates.

Calls the OSRM /table/v1/driving/ endpoint asynchronously.

Passes the distance/duration matrix to the graph_builder.py module to construct a directed networkx graph.

Returns a JSON representation of the graph's nodes and edges using nx.node_link_data.

Endpoint 3 (/match): Accepts a MatchRequest payload to clean up noisy GPS trajectories.

Calls the OSRM /match/v1/driving/ endpoint asynchronously via the osrm_client.

Must handle the architectural edge case of "Trace Splitting" gracefully: if OSRM returns multiple separate match objects (due to large GPS gaps or signal drops), the endpoint must process and return all valid segments without crashing.

Step 6: OSRM Async Client (app/services/osrm_client.py)
Implement an asynchronous wrapper class for the OSRM API using httpx.AsyncClient.

Initialize it with the OSRM_BASE_URL.

Write robust methods catching httpx.HTTPError.

Implement a match_route method that accepts the MatchRequest object:

Formats coordinates as a semicolon-separated string: lon1,lat1;lon2,lat2.

Formats timestamps and radiuses as semicolon-separated strings to feed the Hidden Markov Model algorithm.

Appends query parameters: timestamps, radiuses, overview=full, and geometries=geojson.
