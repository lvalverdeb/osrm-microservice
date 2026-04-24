# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- [feat] **Full OSRM API Coverage**: Implemented all six OSRM services (Route, Table, Match, Trip, Nearest, and Tile).
- [feat] **Multi-modal Support**: Added `profile` parameter to all endpoints, enabling `driving`, `cycling`, and `walking` modes.
- [feat] **Advanced Routing Options**: Exposed core OSRM parameters including `overview`, `geometries`, `steps`, `annotations`, and `continue_straight`.
- [feat] **Nearest Service**: Introduced `/nearest` endpoint for snapping coordinates to the road network.
- [feat] **Tile Proxy**: Added `/tile` endpoint for Mapbox Vector Tile (MVT) pass-through.
- [feat] **Shared Routing Constraints**: Exposed `bearings`, `radiuses`, `hints`, `approaches`, `exclude`, and `snapping` across all relevant services.

### Changed

- [refactor] **Structured Error Handling**: Forwarding OSRM `code` and `message` in error responses instead of generic strings.
- [refactor] **Dynamic Parameterization**: Removed hardcoded OSRM constants in favor of schema-driven request models.

- [feat] Introduced unique `id` field for stops in `/vrp/allocate` endpoint.
- [feat] Added `id` propagation in `VrpService` to return provided stop identifiers in allocation results.
- [feat] Introduced depot ID propagation in `/vrp/allocate` response keys.
- [feat] Enhanced clustering examples (`run_clustering_workflow.py` and `generate_payload.py`) to demonstrate and utilize unique stop IDs.

### Fixed

- [fix] Improved visualization logic in `run_clustering_workflow.py` to handle both the new string IDs and fallback integer indices.
- [fix] Refactored `VrpAllocationResponse` to handle heterogeneous types (`Union[str, int]`) for both stop identifiers and depot keys.
