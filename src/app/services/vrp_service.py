from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.config import settings
from app.models.schemas import (
    Coordinate,
    TripRequest,
    VehicleRoute,
    VrpAllocationResponse,
    VrpRequest,
    VrpResponse,
)
from app.services.osrm_client import OSRMClient

logger = logging.getLogger(__name__)

UNREACHABLE: float = 1e12
"""Sentinel value representing an unreachable route in NumPy distance/duration matrices."""
_MPS_TO_KMH: float = 3.6
"""Conversion factor: m/s to km/h (irrelevant here; kept for clarity)."""
_HYSTERESIS_SPEED_MS: float = 11.1111
"""Assumed average speed (40 km/h) used to convert hysteresis from meters to seconds: 40000 / 3600 ≈ 11.111 m/s."""
DEG_TO_M: float = 111320.0
"""Approximate meters per degree of latitude at the equator."""
KM_TO_M: float = 1000.0
"""Conversion factor: kilometers to meters."""

MODE_TRAVEL_TIME: str = "travel_time"
MODE_DISTANCE: str = "distance"
MODE_RADIAL: str = "radial"
"""Clustering modes accepted by VrpRequest.clustering_mode (validated by the schema's Literal)."""


@dataclass(frozen=True)
class AllocationOptions:
    """Location-Allocation tuning knobs, grouped into one object to keep _allocate_stops's parameter count down."""
    mode: str = MODE_TRAVEL_TIME
    hysteresis_m: float = 2000.0
    max_radius_m: float | None = None
    sanity_limit_m: float | None = None


@dataclass
class _TspChunkRequest:
    """Everything needed to solve one vehicle's TSP chunk, grouped to keep _solve_tsp_chunk's parameter count down."""
    depot_idx: int
    depot: Coordinate
    stops: list[Coordinate]
    original_indices: list[int]
    stop_ids: list[str | int] | None = None
    vehicle_id: str | int = 0
    roundtrip: bool = True


class VrpService:
    """
    Main service for coordinating Location-Allocation and Multi-Vehicle Routing.
    Uses OSRM for both distance measurement and sequence optimization.
    """

    def __init__(self, osrm_client: OSRMClient):
        self.osrm_client = osrm_client

    async def solve_vrp(self, request: VrpRequest) -> VrpResponse:
        """
        Solve multi-vehicle Vehicle Routing Problem.
        1. Allocation: Assign stops to depots based on OSRM travel durations.
        2. Optimization: Solve a TSP (/trip) for each assigned set.
        """
        allocation_result = await self._get_allocation_data(request)
        depot_assignments = allocation_result["allocations"]

        vehicle_routes: list[VehicleRoute] = []
        for depot_idx, stop_indices in depot_assignments.items():
            if not stop_indices:
                continue
            vehicle_routes.extend(
                await self._solve_depot_routes(request, depot_idx, stop_indices, len(vehicle_routes))
            )

        total_dist = sum(r.distance_meters for r in vehicle_routes)
        total_dur = sum(r.duration_seconds for r in vehicle_routes)
        return VrpResponse(routes=vehicle_routes, total_distance=total_dist, total_duration=total_dur)

    async def _solve_depot_routes(
        self, request: VrpRequest, depot_idx: int, stop_indices: list[int], vehicle_offset: int
    ) -> list[VehicleRoute]:
        """Split one depot's assigned stops into TSP-sized chunks and solve each as a vehicle route."""
        current_depot = request.depots[depot_idx]
        depot_id = getattr(current_depot, "id", None)

        # Sub-partition if stops > 100 (OSRM /trip limit) or request.capacity
        chunk_size = min(settings.VRP_CHUNK_SIZE, request.capacity)
        num_chunks = (len(stop_indices) + chunk_size - 1) // chunk_size

        routes: list[VehicleRoute] = []
        for i in range(0, len(stop_indices), chunk_size):
            chunk = stop_indices[i:i + chunk_size]

            # Determine vehicle_id for this route. If there's more than one vehicle
            # from this depot, add a suffix.
            if depot_id is not None:
                vehicle_label = f"{depot_id}-{i // chunk_size + 1}" if num_chunks > 1 else depot_id
            else:
                vehicle_label = vehicle_offset + len(routes)

            # Ensure we pass simplified Coordinates to the TSP solver
            current_stops = [
                Coordinate(latitude=request.stops[idx].latitude, longitude=request.stops[idx].longitude)
                for idx in chunk
            ]

            # Extract IDs for this chunk if they exist
            chunk_ids = [request.stops[idx].id for idx in chunk]
            has_ids = any(cid is not None for cid in chunk_ids)

            chunk_request = _TspChunkRequest(
                depot_idx=int(depot_idx),
                depot=current_depot,
                stops=current_stops,
                original_indices=chunk,
                stop_ids=chunk_ids if has_ids else None,
                vehicle_id=vehicle_label,
                roundtrip=request.roundtrip,
            )
            routes.append(await self._solve_tsp_chunk(chunk_request))

        return routes

    async def allocate_products(self, request: VrpRequest) -> VrpAllocationResponse:
        """
        Stand-alone Allocation phase with ID propagation.
        Assigns each product to its logistically best warehouse and returns ID-mapped results.
        """
        allocation_result = await self._get_allocation_data(request)

        # Map indices back to provided IDs (for both depots and stops)
        depot_ids = [d.id for d in request.depots]
        stop_ids = [s.id for s in request.stops]

        if not any(sid is not None for sid in stop_ids) and not any(did is not None for did in depot_ids):
            # No IDs were supplied anywhere — fall back to raw indices.
            return VrpAllocationResponse(
                allocations=allocation_result["allocations"],
                unreachable_stops=allocation_result["unreachable_stops"]
            )

        id_allocations = {
            self._map_index_to_id(int(d_idx), depot_ids, "depot"): [
                self._map_index_to_id(idx, stop_ids, "stop") for idx in s_indices
            ]
            for d_idx, s_indices in allocation_result["allocations"].items()
        }
        id_unreachable = [
            self._map_index_to_id(idx, stop_ids, "stop") for idx in allocation_result["unreachable_stops"]
        ]

        return VrpAllocationResponse(allocations=id_allocations, unreachable_stops=id_unreachable)

    @staticmethod
    def _map_index_to_id(idx: int, ids: list[str | int], kind: str) -> str | int:
        """Map a raw allocation index back to its caller-supplied ID, falling back to the index itself."""
        if idx < 0 or idx >= len(ids):
            raise ValueError(f"Allocation {kind} index {idx} out of range for {len(ids)} {kind}s")
        return ids[idx] if ids[idx] is not None else idx

    async def _get_allocation_data(self, request: VrpRequest) -> dict[str, Any]:
        """Internal helper to get raw allocation indices for both allocate and solve phases."""
        # 1. Measure durations AND distances from all depots to all stops
        matrix_data = await self._get_depot_to_stop_matrix(request.depots, request.stops)

        # 2. Convert radius to meters (if provided)
        max_radius_m = request.max_radius_km * KM_TO_M if request.max_radius_km else None

        # 3. Assign stops based on Road Distance with Radial Fallback and Hysteresis
        return self._allocate_stops(
            matrix_data["durations"],
            matrix_data["distances"],
            request.depots,
            request.stops,
            AllocationOptions(
                mode=request.clustering_mode,
                hysteresis_m=request.hysteresis_m,
                max_radius_m=max_radius_m,
            ),
        )

    async def _solve_tsp_chunk(self, chunk_request: _TspChunkRequest) -> VehicleRoute:
        """Helper to solve TSP for a small cluster of stops."""
        trip_req = TripRequest(
            coordinates=[chunk_request.depot] + chunk_request.stops,
            roundtrip=chunk_request.roundtrip,
            source="first",
            destination="any"
        )

        trip_result = await self.osrm_client.get_trip(trip_req)

        if trip_result.get("code") != "Ok":
            logger.error("TSP chunk failed: code=%s message=%s", trip_result.get("code"), trip_result.get("message"))
            raise RuntimeError("Failed to optimize TSP chunk")

        best_trip = trip_result["trips"][0]
        waypoints = trip_result.get("waypoints", [])
        optimized_indices, optimized_ids, optimized_coords = self._reorder_waypoints(waypoints, chunk_request)

        return VehicleRoute(
            vehicle_id=chunk_request.vehicle_id,
            depot_index=chunk_request.depot_idx,
            stops_indices=optimized_indices,
            stop_ids=optimized_ids,
            stop_coordinates=optimized_coords,
            route_geometry=best_trip["geometry"],
            distance_meters=best_trip["distance"],
            duration_seconds=best_trip["duration"]
        )

    @staticmethod
    def _reorder_waypoints(waypoints: list[dict[str, Any]], chunk_request: _TspChunkRequest):
        """Reorder OSRM's TSP waypoints and map them back to original stop indices/IDs/coordinates."""
        # sorted_input_indices will be [0, opt_idx1, opt_idx2, ...] where 0 is the depot
        sorted_input_indices = sorted(
            range(len(waypoints)),
            key=lambda i: (waypoints[i].get("trips_index", 0), waypoints[i].get("waypoint_index", 0))
        )
        # Filter out the depot (index 0)
        optimized_stop_input_indices = [idx for idx in sorted_input_indices if idx > 0]

        original_indices = chunk_request.original_indices
        stop_ids = chunk_request.stop_ids
        stops = chunk_request.stops

        optimized_indices = []
        optimized_ids = [] if stop_ids else None
        optimized_coords = []
        for idx in optimized_stop_input_indices:
            mapped = idx - 1
            if mapped < 0 or mapped >= len(original_indices):
                raise ValueError(f"OSRM returned waypoint index {idx} out of range for {len(original_indices)} stops")
            optimized_indices.append(original_indices[mapped])
            if stop_ids is not None:
                if mapped >= len(stop_ids):
                    raise ValueError(f"OSRM returned waypoint index {idx} out of range for {len(stop_ids)} stop IDs")
                optimized_ids.append(stop_ids[mapped])
            if mapped >= len(stops):
                raise ValueError(f"OSRM returned waypoint index {idx} out of range for {len(stops)} coordinates")
            optimized_coords.append(stops[mapped])

        return optimized_indices, optimized_ids, optimized_coords

    async def _get_depot_to_stop_matrix(self, depots: list[Coordinate], stops: list[Coordinate]) -> dict[str, Any]:
        """Fetch the duration and distance matrix from OSRM with batching support."""
        from app.models.schemas import MatrixRequest

        # OSRM Matrix often limits sources*destinations. We batch stops (destinations).
        BATCH_SIZE = settings.MATRIX_BATCH_SIZE
        num_depots = len(depots)
        num_stops = len(stops)

        # Result matrices: [num_depots x num_stops]
        full_durations = [[] for _ in range(num_depots)]
        full_distances = [[] for _ in range(num_depots)]

        for i in range(0, num_stops, BATCH_SIZE):
            chunk = stops[i:i + BATCH_SIZE]
            combined_points = depots + chunk

            sources = list(range(num_depots))
            destinations = list(range(num_depots, len(combined_points)))

            matrix_req = MatrixRequest(
                coordinates=combined_points,
                sources=sources,
                destinations=destinations
            )

            # Get matrix
            matrix_data = await self.osrm_client.get_matrix(matrix_req)

            if "durations" in matrix_data:
                for d_idx, depot_durations in enumerate(matrix_data["durations"]):
                    full_durations[d_idx].extend(depot_durations)

            if "distances" in matrix_data:
                for d_idx, depot_distances in enumerate(matrix_data["distances"]):
                    full_distances[d_idx].extend(depot_distances)

        return {
            "durations": full_durations,
            "distances": full_distances
        }

    def _allocate_stops(
        self,
        durations: list[list[float]],
        distances: list[list[float]],
        depots: list[Coordinate],
        stops: list[Coordinate],
        options: AllocationOptions = AllocationOptions(),
    ) -> dict[str, Any]:
        """
        Location-Allocation logic.
        Assigns each stop to the depot based on the selected mode:
        - 'distance': Shortest Road Distance primary.
        - 'travel_time': Shortest Travel Duration primary.
        - 'radial': Euclidean distance primary.
        """
        num_depots = len(depots)
        assignments = {i: [] for i in range(num_depots)}
        unreachable = []

        dist_np, dur_np = self._prepare_cost_matrices(durations, distances)
        target_matrix = dur_np if options.mode == MODE_TRAVEL_TIME else dist_np
        effective_hysteresis = (
            (options.hysteresis_m / _HYSTERESIS_SPEED_MS) if options.mode == MODE_TRAVEL_TIME else options.hysteresis_m
        )
        sanity_limit_m = options.sanity_limit_m if options.sanity_limit_m is not None else settings.VRP_SANITY_LIMIT_M

        # Compute centroid latitude from all stops for longitude scaling
        centroid_lat = sum(s.latitude for s in stops) / len(stops)
        lon_scale = math.cos(math.radians(centroid_lat))

        for stop_idx in range(dist_np.shape[1]):
            euclidean_m = self._euclidean_distances_m(stops[stop_idx], depots, lon_scale)
            final_depot_idx = self._select_depot(
                stop_idx, euclidean_m, target_matrix, options.mode, effective_hysteresis, sanity_limit_m
            )

            final_road_dist = dist_np[final_depot_idx, stop_idx]
            if options.max_radius_m is not None and final_road_dist > options.max_radius_m and final_road_dist < UNREACHABLE:
                unreachable.append(stop_idx)
            else:
                assignments[final_depot_idx].append(stop_idx)

        return {"allocations": assignments, "unreachable_stops": unreachable}

    @staticmethod
    def _prepare_cost_matrices(durations: list[list[float]], distances: list[list[float]]):
        """Convert raw OSRM matrices to NumPy float arrays, mapping null/unreachable entries to UNREACHABLE."""
        # `== None` (not `is None`) is intentional: these are object-dtype NumPy arrays
        # (OSRM returns JSON null for unreachable pairs) and the comparison must broadcast
        # elementwise to build the replacement mask, which `is None` would not do.
        dist_np = np.array(distances)
        dist_np = np.where(dist_np == None, UNREACHABLE, dist_np).astype(float)

        dur_np = np.array(durations)
        dur_np = np.where(dur_np == None, UNREACHABLE, dur_np).astype(float)

        return dist_np, dur_np

    @staticmethod
    def _euclidean_distances_m(stop: Coordinate, depots: list[Coordinate], lon_scale: float) -> np.ndarray:
        """Vectorized straight-line distance (meters) from one stop to every depot."""
        lat_diffs_m = np.array([d.latitude - stop.latitude for d in depots]) * DEG_TO_M
        lon_diffs_m = np.array([d.longitude - stop.longitude for d in depots]) * DEG_TO_M * lon_scale
        # spaghetti-ignore[magic-number]: exponent in the Euclidean distance formula, not a tunable threshold
        return np.sqrt(lat_diffs_m ** 2 + lon_diffs_m ** 2)

    @staticmethod
    def _select_depot(
        stop_idx: int,
        euclidean_m: np.ndarray,
        target_matrix: np.ndarray,
        mode: str,
        effective_hysteresis: float,
        sanity_limit_m: float,
    ) -> int:
        """Pick the depot for one stop: 'radial' uses the nearest by air distance; other modes
        prefer the cost-matrix optimum unless it fails the visual-sanity or hysteresis checks."""
        anchor_depot_idx = int(np.argmin(euclidean_m))
        if mode == MODE_RADIAL:
            return anchor_depot_idx

        anchor_euclidean_dist = euclidean_m[anchor_depot_idx]
        best_idx = int(np.argmin(target_matrix[:, stop_idx]))
        best_val = target_matrix[best_idx, stop_idx]
        anchor_val = target_matrix[anchor_depot_idx, stop_idx]
        best_euclidean_dist = euclidean_m[best_idx]

        # --- Visual Sanity Check ---
        if (best_euclidean_dist - anchor_euclidean_dist) > sanity_limit_m:
            return anchor_depot_idx
        if best_val >= UNREACHABLE:
            return anchor_depot_idx
        if anchor_val >= UNREACHABLE:
            return best_idx
        if best_val < (anchor_val - effective_hysteresis):
            return best_idx
        return anchor_depot_idx
