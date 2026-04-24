from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Union, Dict, Any

class Coordinate(BaseModel):
    """Standard coordinate model."""
    longitude: float = Field(..., ge=-180, le=180, description="Longitude of the point")
    latitude: float = Field(..., ge=-90, le=90, description="Latitude of the point")

class Stop(Coordinate):
    """Specific delivery stop with an optional ID."""
    id: Optional[Union[str, int]] = Field(None, description="Unique identifier for the stop used for tracking")

# Type alias used by Route, Match, and Trip annotation fields
AnnotationValue = Literal[
    "true", "false", "nodes", "distance",
    "duration", "datasources", "weight", "speed"
]

# Restricted annotation subset accepted by the Table service
MatrixAnnotation = Literal["duration", "distance", "duration,distance"]

class CommonRoutingOptions(BaseModel):
    """
    Optional OSRM general options applicable to Route, Table, Match, and Trip services.
    All fields are optional and omitted from the upstream request when not set.
    Per-coordinate lists must have exactly one entry per coordinate (or be omitted entirely).
    """
    bearings: Optional[List[Optional[str]]] = Field(
        None,
        description="Per-coordinate bearing constraints as 'angle,deviation' strings (e.g. '90,30'). Use null for unconstrained coordinates."
    )
    radiuses: Optional[List[Optional[float]]] = Field(
        None,
        description="Per-coordinate snapping radius in meters. Use null for unlimited."
    )
    hints: Optional[List[Optional[str]]] = Field(
        None,
        description="Per-coordinate hint strings from a previous OSRM response to speed up snapping."
    )
    approaches: Optional[List[Optional[Literal["unrestricted", "curb"]]]] = Field(
        None,
        description="Per-coordinate approach side. Use null for default."
    )
    exclude: Optional[List[str]] = Field(
        None,
        description="Road classes to exclude globally (e.g. ['motorway', 'toll'])."
    )
    snapping: Optional[Literal["default", "any"]] = Field(
        None,
        description="Edge selection behaviour: 'default' (one-way roads respected) or 'any'."
    )
    skip_waypoints: Optional[bool] = Field(
        None,
        description="Suppress the waypoints array in the response to reduce payload size."
    )

class RouteRequest(CommonRoutingOptions):
    """Request model for routing between points."""
    origin: Coordinate
    destination: Coordinate
    waypoints: Optional[List[Coordinate]] = Field(None, max_length=200, description="Optional intermediate waypoints")
    alternatives: Union[bool, int] = Field(False, description="Number of alternate routes to return")
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
    geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
    steps: bool = Field(True, description="Return step-by-step turn instructions")
    annotations: Optional[str] = Field("distance,duration", description="Comma-separated metadata to annotate each segment (distance, duration, nodes, datasources, weight, speed)")
    continue_straight: Optional[Literal["default", "true", "false"]] = Field(None, description="Force route to continue straight at intermediate waypoints")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "origin": {"longitude": -84.0907, "latitude": 9.9281},
                    "destination": {"longitude": -84.0833, "latitude": 9.9333},
                    "profile": "driving",
                    "geometries": "geojson",
                    "steps": True
                }
            ]
        }
    }

class GPSBreadcrumb(BaseModel):
    """Model for a single GPS breadcrumb."""
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    timestamp: int = Field(..., ge=0, description="Unix timestamp (integer)")
    accuracy_meters: Optional[float] = Field(5.0, gt=0, description="Optional accuracy in meters")

class MatchRequest(CommonRoutingOptions):
    """Request model for map matching a sequence of GPS breadcrumbs."""
    breadcrumbs: List[GPSBreadcrumb] = Field(..., min_length=2, max_length=5000)
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
    geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
    steps: bool = Field(False, description="Return step-by-step instructions for the matched route")
    annotations: Optional[str] = Field(None, description="Comma-separated metadata annotations per segment")
    gaps: Optional[Literal["split", "ignore"]] = Field(None, description="How to handle large timestamp gaps in the trace")
    tidy: Optional[bool] = Field(None, description="Remove repeated or out-of-order coordinates before matching")
    match_waypoints: Optional[List[int]] = Field(None, description="Indices into breadcrumbs to treat as explicit waypoints")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "breadcrumbs": [
                        {"longitude": -84.0907, "latitude": 9.9281, "timestamp": 1713000000, "accuracy_meters": 5.0},
                        {"longitude": -84.0880, "latitude": 9.9300, "timestamp": 1713000030, "accuracy_meters": 5.0},
                        {"longitude": -84.0833, "latitude": 9.9333, "timestamp": 1713000060, "accuracy_meters": 5.0}
                    ],
                    "profile": "driving",
                    "geometries": "geojson",
                    "tidy": True
                }
            ]
        }
    }

class MatrixRequest(CommonRoutingOptions):
    """Request model for generating a distance matrix."""
    coordinates: List[Coordinate] = Field(..., min_length=2, max_length=5000)
    sources: Optional[List[int]] = None
    destinations: Optional[List[int]] = None
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    annotations: MatrixAnnotation = Field("duration,distance", description="Which cost annotations to return")
    fallback_speed: Optional[float] = Field(None, gt=0, description="Speed (km/h) used to estimate travel time for unreachable pairs")
    fallback_coordinate: Optional[Literal["input", "snapped"]] = Field(None, description="Which coordinate to use when a pair falls back to speed estimate")
    scale_factor: Optional[float] = Field(None, gt=0, description="Multiply all durations by this factor (useful for congestion modelling)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "coordinates": [
                        {"longitude": -84.0907, "latitude": 9.9281},
                        {"longitude": -84.0833, "latitude": 9.9333},
                        {"longitude": -84.1000, "latitude": 9.9400}
                    ],
                    "profile": "driving",
                    "annotations": "duration,distance"
                }
            ]
        }
    }

class MatrixGraphResponse(BaseModel):
    """Schema for returning adjacency lists/graph data."""
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]

class TripRequest(CommonRoutingOptions):
    """Request model for TSP optimization (Trip service)."""
    coordinates: List[Coordinate] = Field(..., min_length=2, max_length=200)
    roundtrip: bool = Field(True, description="Whether the trip should return to the first point")
    source: Literal["first", "any"] = Field("first", description="Where the trip starts (first, any)")
    destination: Literal["last", "any"] = Field("last", description="Where the trip ends (last, any)")
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
    geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
    steps: bool = Field(True, description="Return step-by-step turn instructions")
    annotations: Optional[str] = Field("distance,duration", description="Comma-separated metadata annotations per segment")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "coordinates": [
                        {"longitude": -84.0907, "latitude": 9.9281},
                        {"longitude": -84.0833, "latitude": 9.9333},
                        {"longitude": -84.1000, "latitude": 9.9400}
                    ],
                    "roundtrip": True,
                    "source": "first",
                    "destination": "last",
                    "profile": "driving",
                    "geometries": "geojson"
                }
            ]
        }
    }

class NearestRequest(CommonRoutingOptions):
    """Request model for the OSRM Nearest service."""
    coordinate: Coordinate
    number: int = Field(1, ge=1, description="Number of nearest road segments to return")
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
                    "number": 3,
                    "profile": "driving"
                }
            ]
        }
    }

class NearestResponse(BaseModel):
    """Response model for the OSRM Nearest service (pass-through)."""
    code: str
    waypoints: List[Dict[str, Any]]

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

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "depots": [
                        {"id": "DEPOT-A", "longitude": -84.0907, "latitude": 9.9281}
                    ],
                    "stops": [
                        {"id": "STOP-1", "longitude": -84.0833, "latitude": 9.9333},
                        {"id": "STOP-2", "longitude": -84.1000, "latitude": 9.9400}
                    ],
                    "vehicle_count": 1,
                    "capacity": 35,
                    "clustering_mode": "travel_time"
                }
            ]
        }
    }

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
