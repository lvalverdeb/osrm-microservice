from typing import Any, Literal

from pydantic import BaseModel, Field


class Coordinate(BaseModel):
    """Standard coordinate model."""
    longitude: float = Field(..., ge=-180, le=180, description="Longitude of the point")
    latitude: float = Field(..., ge=-90, le=90, description="Latitude of the point")

# spaghetti-ignore[lazy-class]: Pydantic request model; methodless by design
class Stop(Coordinate):
    """Specific delivery stop with an optional ID."""
    id: str | int | None = Field(None, description="Unique identifier for the stop used for tracking")

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
    bearings: list[str | None] | None = Field(
        None,
        description="Per-coordinate bearing constraints as 'angle,deviation' strings (e.g. '90,30'). Use null for unconstrained coordinates."
    )
    radiuses: list[float | None] | None = Field(
        None,
        description="Per-coordinate snapping radius in meters. Use null for unlimited."
    )
    hints: list[str | None] | None = Field(
        None,
        description="Per-coordinate hint strings from a previous OSRM response to speed up snapping."
    )
    approaches: list[Literal["unrestricted", "curb"] | None] | None = Field(
        None,
        description="Per-coordinate approach side. Use null for default."
    )
    exclude: list[str] | None = Field(
        None,
        description="Road classes to exclude globally (e.g. ['motorway', 'toll'])."
    )
    snapping: Literal["default", "any"] | None = Field(
        None,
        description="Edge selection behaviour: 'default' (one-way roads respected) or 'any'."
    )
    skip_waypoints: bool | None = Field(
        None,
        description="Suppress the waypoints array in the response to reduce payload size."
    )

# spaghetti-ignore[lazy-class]: Pydantic request model; methodless by design
class RouteRequest(CommonRoutingOptions):
    """Request model for routing between points."""
    origin: Coordinate
    destination: Coordinate
    waypoints: list[Coordinate] | None = Field(None, max_length=200, description="Optional intermediate waypoints")
    alternatives: bool | int = Field(False, description="Number of alternate routes to return")
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
    geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
    steps: bool = Field(True, description="Return step-by-step turn instructions")
    annotations: str | None = Field("distance,duration", description="Comma-separated metadata to annotate each segment (distance, duration, nodes, datasources, weight, speed)")
    continue_straight: Literal["default", "true", "false"] | None = Field(None, description="Force route to continue straight at intermediate waypoints")
    
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
    accuracy_meters: float | None = Field(5.0, gt=0, description="Optional accuracy in meters")

# spaghetti-ignore[lazy-class]: Pydantic request model; methodless by design
class MatchRequest(CommonRoutingOptions):
    """Request model for map matching a sequence of GPS breadcrumbs."""
    breadcrumbs: list[GPSBreadcrumb] = Field(..., min_length=2, max_length=5000)
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
    geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
    steps: bool = Field(False, description="Return step-by-step instructions for the matched route")
    annotations: str | None = Field(None, description="Comma-separated metadata annotations per segment")
    gaps: Literal["split", "ignore"] | None = Field(None, description="How to handle large timestamp gaps in the trace")
    tidy: bool | None = Field(None, description="Remove repeated or out-of-order coordinates before matching")
    match_waypoints: list[int] | None = Field(None, description="Indices into breadcrumbs to treat as explicit waypoints")
    
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

# spaghetti-ignore[lazy-class]: Pydantic request model; methodless by design
class MatrixRequest(CommonRoutingOptions):
    """Request model for generating a distance matrix."""
    coordinates: list[Coordinate] = Field(..., min_length=2, max_length=5000)
    sources: list[int] | None = None
    destinations: list[int] | None = None
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    annotations: MatrixAnnotation = Field("duration,distance", description="Which cost annotations to return")
    fallback_speed: float | None = Field(None, gt=0, description="Speed (km/h) used to estimate travel time for unreachable pairs")
    fallback_coordinate: Literal["input", "snapped"] | None = Field(None, description="Which coordinate to use when a pair falls back to speed estimate")
    scale_factor: float | None = Field(None, gt=0, description="Multiply all durations by this factor (useful for congestion modelling)")

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
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]

# spaghetti-ignore[lazy-class]: Pydantic request model; methodless by design
class TripRequest(CommonRoutingOptions):
    """Request model for TSP optimization (Trip service)."""
    coordinates: list[Coordinate] = Field(..., min_length=2, max_length=200)
    roundtrip: bool = Field(True, description="Whether the trip should return to the first point")
    source: Literal["first", "any"] = Field("first", description="Where the trip starts (first, any)")
    destination: Literal["last", "any"] = Field("last", description="Where the trip ends (last, any)")
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
    overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
    geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
    steps: bool = Field(True, description="Return step-by-step turn instructions")
    annotations: str | None = Field("distance,duration", description="Comma-separated metadata annotations per segment")

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

# spaghetti-ignore[lazy-class]: Pydantic request model; methodless by design
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
    waypoints: list[dict[str, Any]]

class VrpRequest(BaseModel):
    """Request model for Vehicle Routing Problem (VRP)."""
    depots: list[Stop] = Field(..., min_length=1, max_length=500, description="List of warehouse/depot locations")
    stops: list[Stop] = Field(..., min_length=1, max_length=10000, description="List of delivery points")
    vehicle_count: int | None = Field(None, gt=0, description="Total vehicles available. Defaults to one per depot.")
    capacity: int = Field(35, gt=0, le=10000, description="Maximum packages a single vehicle can carry")
    max_radius_km: float | None = Field(None, gt=0, description="Optional maximum road distance from depot (km)")
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
    vehicle_id: str | int
    depot_index: int
    stops_indices: list[int]
    stop_ids: list[str | int] | None = None
    stop_coordinates: list[Coordinate] | None = None
    route_geometry: dict[str, Any]
    distance_meters: float
    duration_seconds: float

class VrpAllocationResponse(BaseModel):
    """Response model for the Location-Allocation (Clustering) phase."""
    code: str = "Ok"
    # depot_id/index -> list of stop identifiers (IDs or Indices)
    allocations: dict[str | int, list[str | int]]
    unreachable_stops: list[str | int]
    # Optionally return the distance matrix if needed for downstream
    # distances: Optional[List[List[float]]] = None

class VrpResponse(BaseModel):
    """Response model for the VRP solution."""
    code: str = "Ok"
    routes: list[VehicleRoute]
    total_distance: float
    total_duration: float
