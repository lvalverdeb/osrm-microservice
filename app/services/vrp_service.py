from typing import List, Dict, Any, Tuple
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
        # 1. Reuse the allocation logic
        allocation_data = await self.allocate_products(request)
        depot_assignments = allocation_data.allocations
        
        # 2. Solve TSP for each depot's cluster
        vehicle_routes = []
        total_dist = 0
        total_dur = 0
        
        for depot_idx, stop_indices in depot_assignments.items():
            if not stop_indices:
                continue
                
            current_depot = request.depots[depot_idx]
            
            # 2.1 Sub-partition if stops > 100 (OSRM /trip limit) or request.capacity
            CHUNK_SIZE = min(80, request.capacity)
            for i in range(0, len(stop_indices), CHUNK_SIZE):
                chunk = stop_indices[i:i + CHUNK_SIZE]
                current_stops = [request.stops[idx] for idx in chunk]
                
                route = await self._solve_tsp_chunk(int(depot_idx), current_depot, current_stops, chunk)
                vehicle_routes.append(route)
                total_dist += route.distance_meters
                total_dur += route.duration_seconds
                
        return VrpResponse(
            routes=vehicle_routes,
            total_distance=total_dist,
            total_duration=total_dur
        )

    async def allocate_products(self, request: VrpRequest) -> "VrpAllocationResponse":
        """
        Stand-alone Allocation phase.
        Assigns each product to its logistically best warehouse based on road distance.
        """
        from app.models.schemas import VrpAllocationResponse
        
        # 1. Measure durations AND distances from all depots to all stops
        matrix_data = await self._get_depot_to_stop_matrix(request.depots, request.stops)
        durations = matrix_data["durations"]
        distances = matrix_data["distances"]
        
        # 2. Convert radius to meters (if provided)
        max_radius_m = request.max_radius_km * 1000 if request.max_radius_km else None
        
        # 3. Assign stops based on Road Distance with Radial Fallback and Hysteresis
        allocation_result = self._allocate_stops(
            durations, 
            distances,
            request.depots,
            request.stops,
            max_radius_m=max_radius_m,
            mode=request.clustering_mode,
            hysteresis_m=request.hysteresis_m
        )
        
        return VrpAllocationResponse(
            allocations=allocation_result["allocations"],
            unreachable_stops=allocation_result["unreachable_stops"]
        )

    async def _solve_tsp_chunk(self, depot_idx: int, depot: Coordinate, stops: List[Coordinate], original_indices: List[int]) -> VehicleRoute:
        """Helper to solve TSP for a small cluster of stops."""
        trip_req = TripRequest(
            coordinates=[depot] + stops,
            roundtrip=True,
            source="first",
            destination="any"
        )
        
        trip_result = await self.osrm_client.get_trip(trip_req)
        
        if trip_result.get("code") == "Ok":
            best_trip = trip_result["trips"][0]
            return VehicleRoute(
                vehicle_id=0, # Placeholder, will be updated by caller
                depot_index=depot_idx,
                stops_indices=original_indices,
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
