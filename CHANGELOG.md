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
- Multi-stage `Dockerfile.builder` for processing OSRM data without host volumes.
- Optimized `Dockerfile.osrm` for bundling processed data into production images.
