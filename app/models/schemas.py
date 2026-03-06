from pydantic import BaseModel, Field
from typing import List, Optional, Union, Dict, Any

class Coordinate(BaseModel):
    """Standard coordinate model."""
    longitude: float = Field(..., description="Longitude of the point")
    latitude: float = Field(..., description="Latitude of the point")

class RouteRequest(BaseModel):
    """Request model for routing between points."""
    origin: Coordinate
    destination: Coordinate
    waypoints: Optional[List[Coordinate]] = Field(None, description="Optional intermediate waypoints")
    alternatives: Union[bool, int] = Field(False, description="Number of alternate routes to return")

class GPSBreadcrumb(BaseModel):
    """Model for a single GPS breadcrumb."""
    longitude: float
    latitude: float
    timestamp: int = Field(..., description="Unix timestamp (integer)")
    accuracy_meters: Optional[float] = Field(5.0, description="Optional accuracy in meters")

class MatchRequest(BaseModel):
    """Request model for map matching a sequence of GPS breadcrumbs."""
    breadcrumbs: List[GPSBreadcrumb]

class MatrixRequest(BaseModel):
    """Request model for generating a distance matrix."""
    coordinates: List[Coordinate]
    sources: Optional[List[int]] = None
    destinations: Optional[List[int]] = None

class MatrixGraphResponse(BaseModel):
    """Schema for returning adjacency lists/graph data."""
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]

class TripRequest(BaseModel):
    """Request model for TSP optimization (Trip service)."""
    coordinates: List[Coordinate]
    roundtrip: bool = Field(True, description="Whether the trip should return to the first point")
    source: str = Field("first", description="Where the trip starts (first, any)")
    destination: str = Field("last", description="Where the trip ends (last, any)")

class VrpRequest(BaseModel):
    """Request model for Vehicle Routing Problem (VRP)."""
    depots: List[Coordinate] = Field(..., description="List of warehouse/depot locations")
    stops: List[Coordinate] = Field(..., description="List of delivery points")
    vehicle_count: Optional[int] = Field(None, description="Total vehicles available. Defaults to one per depot.")
    capacity: int = Field(35, description="Maximum packages a single vehicle can carry")
    max_radius_km: Optional[float] = Field(None, description="Optional maximum road distance from depot (km)")
    clustering_mode: Optional[str] = Field("travel_time", description="Clustering preference: 'distance', 'travel_time', or 'radial'")
    hysteresis_m: float = Field(2000.0, description="Buffer distance to prevent assignment flipping (meters)")

class VehicleRoute(BaseModel):
    """Response model for a single vehicle's optimized route."""
    vehicle_id: int
    depot_index: int
    stops_indices: List[int]
    route_geometry: Dict[str, Any]
    distance_meters: float
    duration_seconds: float

class VrpAllocationResponse(BaseModel):
    """Response model for the Location-Allocation (Clustering) phase."""
    code: str = "Ok"
    # depot_index -> list of stop_indices
    allocations: Dict[int, List[int]]
    unreachable_stops: List[int]
    # Optionally return the distance matrix if needed for downstream
    # distances: Optional[List[List[float]]] = None

class VrpResponse(BaseModel):
    """Response model for the VRP solution."""
    code: str = "Ok"
    routes: List[VehicleRoute]
    total_distance: float
    total_duration: float
