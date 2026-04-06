# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- [feat] Introduced unique `id` field for stops in `/vrp/allocate` endpoint.
- [feat] Added `id` propagation in `VrpService` to return provided stop identifiers in allocation results.
- [feat] Introduced depot ID propagation in `/vrp/allocate` response keys.
- [feat] Enhanced clustering examples (`run_clustering_workflow.py` and `generate_payload.py`) to demonstrate and utilize unique stop IDs.

### Fixed

- [fix] Improved visualization logic in `run_clustering_workflow.py` to handle both the new string IDs and fallback integer indices.
- [fix] Refactored `VrpAllocationResponse` to handle heterogeneous types (`Union[str, int]`) for both stop identifiers and depot keys.
