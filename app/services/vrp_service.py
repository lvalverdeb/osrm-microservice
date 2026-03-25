from __future__ import annotations
from typing import List, Dict, Any, Tuple, Union
import logging
import numpy as np
from app.models.schemas import VrpRequest, VrpResponse, VehicleRoute, TripRequest, Coordinate
from app.services.osrm_client import OSRMClient

logger = logging.getLogger(__name__)

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
        # 1. Reuse the internal allocation logic (always returns indices)
        allocation_result = await self._get_allocation_data(request)
        depot_assignments = allocation_result["allocations"]
        
        # 2. Solve TSP for each depot's cluster
        vehicle_routes = []
        total_dist = 0
        total_dur = 0
        vehicle_counter = 0
        
        for depot_idx, stop_indices in depot_assignments.items():
            if not stop_indices:
                continue
                
            current_depot = request.depots[depot_idx]
            depot_id = getattr(current_depot, "id", None)
            
            # 2.1 Sub-partition if stops > 100 (OSRM /trip limit) or request.capacity
            CHUNK_SIZE = min(80, request.capacity)
            num_chunks = (len(stop_indices) + CHUNK_SIZE - 1) // CHUNK_SIZE
            
            for i in range(0, len(stop_indices), CHUNK_SIZE):
                chunk = stop_indices[i:i + CHUNK_SIZE]
                
                # Determine vehicle_id for this route
                if depot_id is not None:
                    # If there's more than one vehicle from this depot, add a suffix
                    vehicle_label = f"{depot_id}-{i // CHUNK_SIZE + 1}" if num_chunks > 1 else depot_id
                else:
                    vehicle_label = vehicle_counter
                
                # Ensure we pass simplified Coordinates to the TSP solver
                current_stops = [
                    Coordinate(latitude=request.stops[idx].latitude, longitude=request.stops[idx].longitude) 
                    for idx in chunk
                ]
                
                # Extract IDs for this chunk if they exist
                chunk_ids = [request.stops[idx].id for idx in chunk]
                has_ids = any(cid is not None for cid in chunk_ids)
                
                route = await self._solve_tsp_chunk(
                    int(depot_idx), 
                    current_depot, 
                    current_stops, 
                    chunk,
                    stop_ids=chunk_ids if has_ids else None,
                    vehicle_id=vehicle_label,
                    roundtrip=request.roundtrip
                )
                vehicle_routes.append(route)
                total_dist += route.distance_meters
                total_dur += route.duration_seconds
                vehicle_counter += 1
                
        return VrpResponse(
            routes=vehicle_routes,
            total_distance=total_dist,
            total_duration=total_dur
        )

    async def allocate_products(self, request: VrpRequest) -> "VrpAllocationResponse":
        """
        Stand-alone Allocation phase with ID propagation.
        Assigns each product to its logistically best warehouse and returns ID-mapped results.
        """
        from app.models.schemas import VrpAllocationResponse
        
        # 1. Get raw allocation (indices)
        allocation_result = await self._get_allocation_data(request)
        
        # 2. Map indices back to provided IDs if they exist
        stop_ids = [s.id for s in request.stops]
        has_any_id = any(sid is not None for sid in stop_ids)
        
        if has_any_id:
            # Map allocations: depot_index -> list of stop identifiers
            raw_allocations = allocation_result["allocations"]
            id_allocations = {}
            for d_idx, s_indices in raw_allocations.items():
                id_allocations[int(d_idx)] = [
                    stop_ids[idx] if stop_ids[idx] is not None else idx 
                    for idx in s_indices
                ]
            
            # Map unreachable stops
            id_unreachable = [
                stop_ids[idx] if stop_ids[idx] is not None else idx 
                for idx in allocation_result["unreachable_stops"]
            ]
            
            return VrpAllocationResponse(
                allocations=id_allocations,
                unreachable_stops=id_unreachable
            )
        
        # Default fallback to indices
        return VrpAllocationResponse(
            allocations=allocation_result["allocations"],
            unreachable_stops=allocation_result["unreachable_stops"]
        )

    async def _get_allocation_data(self, request: VrpRequest) -> Dict[str, Any]:
        """Internal helper to get raw allocation indices for both allocate and solve phases."""
        # 1. Measure durations AND distances from all depots to all stops
        matrix_data = await self._get_depot_to_stop_matrix(request.depots, request.stops)
        durations = matrix_data["durations"]
        distances = matrix_data["distances"]
        
        # 2. Convert radius to meters (if provided)
        max_radius_m = request.max_radius_km * 1000 if request.max_radius_km else None
        
        # 3. Assign stops based on Road Distance with Radial Fallback and Hysteresis
        return self._allocate_stops(
            durations, 
            distances,
            request.depots,
            request.stops,
            max_radius_m=max_radius_m,
            mode=request.clustering_mode,
            hysteresis_m=request.hysteresis_m
        )

    async def _solve_tsp_chunk(
        self, 
        depot_idx: int, 
        depot: Coordinate, 
        stops: List[Coordinate], 
        original_indices: List[int],
        stop_ids: List[Union[str, int]] = None,
        vehicle_id: Union[str, int] = 0,
        roundtrip: bool = True
    ) -> VehicleRoute:
        """Helper to solve TSP for a small cluster of stops."""
        trip_req = TripRequest(
            coordinates=[depot] + stops,
            roundtrip=roundtrip,
            source="first",
            destination="any"
        )
        
        trip_result = await self.osrm_client.get_trip(trip_req)
        
        if trip_result.get("code") == "Ok":
            best_trip = trip_result["trips"][0]
            waypoints = trip_result.get("waypoints", [])
            
            # Reorder indices based on TSP optimization
            # sorted_input_indices will be [0, opt_idx1, opt_idx2, ...] where 0 is the depot
            sorted_input_indices = sorted(
                range(len(waypoints)),
                key=lambda i: (waypoints[i].get("trips_index", 0), waypoints[i].get("waypoint_index", 0))
            )
            
            # Filter out the depot (index 0) and map back to original indices/IDs
            optimized_stop_input_indices = [idx for idx in sorted_input_indices if idx > 0]
            
            optimized_indices = [original_indices[idx-1] for idx in optimized_stop_input_indices]
            optimized_ids = None
            if stop_ids:
                optimized_ids = [stop_ids[idx-1] for idx in optimized_stop_input_indices]
            
            optimized_coords = [stops[idx-1] for idx in optimized_stop_input_indices]
            
            return VehicleRoute(
                vehicle_id=vehicle_id,
                depot_index=depot_idx,
                stops_indices=optimized_indices,
                stop_ids=optimized_ids,
                stop_coordinates=optimized_coords,
                route_geometry=best_trip["geometry"],
                distance_meters=best_trip["distance"],
                duration_seconds=best_trip["duration"]
            )
        else:
            raise Exception(f"Failed to optimize TSP chunk: {trip_result.get('message')}")

    async def _get_depot_to_stop_matrix(self, depots: List[Coordinate], stops: List[Coordinate]) -> Dict[str, Any]:
        """Fetch the duration and distance matrix from OSRM with batching support."""
        from app.models.schemas import MatrixRequest
        
        # OSRM Matrix often limits sources*destinations. We batch stops (destinations).
        BATCH_SIZE = 500 
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
        durations: List[List[float]], 
        distances: List[List[float]], 
        depots: List[Coordinate], 
        stops: List[Coordinate], 
        max_radius_m: "Optional[float]" = None,
        mode: str = "travel_time",
        hysteresis_m: float = 2000.0,
        sanity_limit_m: float = 50000.0
    ) -> Dict[str, Any]:
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
        
        # 1. Prepare Matrices
        # Road Distance
        dist_np = np.array(distances)
        dist_np = np.where(dist_np == None, 1e12, dist_np).astype(float)
        
        # Travel Duration (Time)
        dur_np = np.array(durations)
        dur_np = np.where(dur_np == None, 1e12, dur_np).astype(float)

        # Decide which matrix to use for 'best' calculation
        # Map user-friendly names to matrices
        target_matrix = dur_np if mode == "travel_time" else dist_np
        
        # Hysteresis in time (if mode=travel_time): 2km at 40km/h is approx 180 seconds
        effective_hysteresis = (hysteresis_m / 11.1) if mode == "travel_time" else hysteresis_m

        for stop_idx in range(dist_np.shape[1]):
            stop_coord = stops[stop_idx]
            
            # Anchor (Visual baseline)
            DEG_TO_M = 110600.0
            euclidean_m = []
            for d in depots:
                d_lat = (d.latitude - stop_coord.latitude) * DEG_TO_M
                d_lon = (d.longitude - stop_coord.longitude) * DEG_TO_M * 0.98
                euclidean_m.append(np.sqrt(d_lat**2 + d_lon**2))
            
            anchor_depot_idx = np.argmin(euclidean_m)
            anchor_euclidean_dist = euclidean_m[anchor_depot_idx]
            
            if mode == "radial":
                assignments[int(anchor_depot_idx)].append(stop_idx)
                continue
                
            # Best choice in target metric (Time or Distance)
            best_idx = np.argmin(target_matrix[:, stop_idx])
            best_val = target_matrix[best_idx, stop_idx]
            anchor_val = target_matrix[anchor_depot_idx, stop_idx]
            
            # Road reachability check (always use distance for this)
            best_road_dist = dist_np[best_idx, stop_idx]
            
            # --- Visual Sanity Check ---
            best_euclidean_dist = euclidean_m[best_idx]
            if (best_euclidean_dist - anchor_euclidean_dist) > sanity_limit_m:
                 final_depot_idx = anchor_depot_idx
            elif best_val >= 1e12:
                final_depot_idx = anchor_depot_idx
            elif anchor_val >= 1e12:
                final_depot_idx = best_idx
            else:
                # HYSTERESIS
                if best_val < (anchor_val - effective_hysteresis):
                    final_depot_idx = best_idx
                else:
                    final_depot_idx = anchor_depot_idx
            
            # Apply maximum radius constraint if requested (still road distance based)
            final_road_dist = dist_np[final_depot_idx, stop_idx]
            if max_radius_m is not None and final_road_dist > max_radius_m and final_road_dist < 1e12:
                unreachable.append(stop_idx)
            else:
                assignments[int(final_depot_idx)].append(stop_idx)
            
        return {"allocations": assignments, "unreachable_stops": unreachable}
