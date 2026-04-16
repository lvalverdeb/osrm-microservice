from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Union, Dict, Any

class Coordinate(BaseModel):
    """Standard coordinate model."""
    longitude: float = Field(..., ge=-180, le=180, description="Longitude of the point")
    latitude: float = Field(..., ge=-90, le=90, description="Latitude of the point")

class Stop(Coordinate):
    """Specific delivery stop with an optional ID."""
    id: Optional[Union[str, int]] = Field(None, description="Unique identifier for the stop used for tracking")

class RouteRequest(BaseModel):
    """Request model for routing between points."""
    origin: Coordinate
    destination: Coordinate
    waypoints: Optional[List[Coordinate]] = Field(None, max_length=50, description="Optional intermediate waypoints")
    alternatives: Union[bool, int] = Field(False, description="Number of alternate routes to return")

class GPSBreadcrumb(BaseModel):
    """Model for a single GPS breadcrumb."""
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    timestamp: int = Field(..., ge=0, description="Unix timestamp (integer)")
    accuracy_meters: Optional[float] = Field(5.0, gt=0, description="Optional accuracy in meters")

class MatchRequest(BaseModel):
    """Request model for map matching a sequence of GPS breadcrumbs."""
    breadcrumbs: List[GPSBreadcrumb] = Field(..., min_length=2, max_length=5000)

class MatrixRequest(BaseModel):
    """Request model for generating a distance matrix."""
    coordinates: List[Coordinate] = Field(..., min_length=2, max_length=5000)
    sources: Optional[List[int]] = None
    destinations: Optional[List[int]] = None

class MatrixGraphResponse(BaseModel):
    """Schema for returning adjacency lists/graph data."""
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]

class TripRequest(BaseModel):
    """Request model for TSP optimization (Trip service)."""
    coordinates: List[Coordinate] = Field(..., min_length=2, max_length=100)
    roundtrip: bool = Field(True, description="Whether the trip should return to the first point")
    source: Literal["first", "any"] = Field("first", description="Where the trip starts (first, any)")
    destination: Literal["last", "any"] = Field("last", description="Where the trip ends (last, any)")

class VrpRequest(BaseModel):
    """Request model for Vehicle Routing Problem (VRP)."""
    depots: List[Stop] = Field(..., min_length=1, max_length=500, description="List of warehouse/depot locations")
    stops: List[Stop] = Field(..., min_length=1, max_length=10000, description="List of delivery points")
    vehicle_count: Optional[int] = Field(None, gt=0, description="Total vehicles available. Defaults to one per depot.")
    capacity: int = Field(35, gt=0, le=10000, description="Maximum packages a single vehicle can carry")
    max_radius_km: Optional[float] = Field(None, gt=0, description="Optional maximum road distance from depot (km)")
    clustering_mode: Literal["distance", "travel_time", "radial"] = Field("travel_time", description="Clustering preference: 'distance', 'travel_time', or 'radial'")
    hysteresis_m: float = Field(2000.0, ge=0, description="Buffer distance to prevent assignment flipping (meters)")
    roundtrip: bool = Field(True, description="Whether each vehicle should return to the depot at the end of its route")

class VehicleRoute(BaseModel):
    """Response model for a single vehicle's optimized route."""
    vehicle_id: Union[str, int]
    depot_index: int
    stops_indices: List[int]
    stop_ids: Optional[List[Union[str, int]]] = None
    stop_coordinates: Optional[List[Coordinate]] = None
    route_geometry: Dict[str, Any]
    distance_meters: float
    duration_seconds: float

class VrpAllocationResponse(BaseModel):
    """Response model for the Location-Allocation (Clustering) phase."""
    code: str = "Ok"
    # depot_id/index -> list of stop identifiers (IDs or Indices)
    allocations: Dict[Union[str, int], List[Union[str, int]]]
    unreachable_stops: List[Union[str, int]]
    # Optionally return the distance matrix if needed for downstream
    # distances: Optional[List[List[float]]] = None

class VrpResponse(BaseModel):
    """Response model for the VRP solution."""
    code: str = "Ok"
    routes: List[VehicleRoute]
    total_distance: float
    total_duration: float
