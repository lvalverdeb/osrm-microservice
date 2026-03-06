# Changelog

## [Unreleased]

### Added

- Initial project structure for OSRM Backend Microservice.
- `Makefile` for automated Costa Rica OSM data acquisition and OSRM MLD processing.
- `docker-compose.yml` for orchestrating the OSRM engine and FastAPI gateway.
- FastAPI application in `app/main.py` with endpoints for `/route`, `/matrix-graph`, and `/match`.
- Asynchronous `OSRMClient` with support for Hidden Markov Model (HMM) map matching.
- `GraphBuilder` service for converting OSRM distance tables to NetworkX graphs.
- Pydantic V2 models for robust request validation and type safety.
- Local agent skills for `fastapi-pro`, `docker-expert`, `uv-package-manager`, and more.
- Hybrid **Local Build & Bundled Transfer** workflow to support remote Docker hosts.
- Multi-stage `Dockerfile.builder` with `ARG PROFILE` support for `car`, `bicycle`, and `foot`.
- Parameterized `Makefile` for profile-specific OSRM data processing.
- Optimized `Dockerfile.osrm` using multi-stage copying for reliable data transfer.
- Support for OSRM Trip service (TSP optimization) via `/trip` endpoint.
- Support for Vehicle Routing Problem (VRP) using **Location-Allocation** via `/vrp` endpoint.
- Support for integer `alternatives` in the `/route` endpoint.
- Interactive visualization tool `visualize_routes.py` for route comparison.
- Comparison tool `compare_tsp.py` for evaluating actual vs optimized round-trips.
- New `visualize_vrp.py` tool for multi-vehicle fleet routing visualization.
