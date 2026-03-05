from pydantic import BaseModel, Field
from typing import List, Optional

class Coordinate(BaseModel):
    """Standard coordinate model."""
    longitude: float = Field(..., description="Longitude of the point")
    latitude: float = Field(..., description="Latitude of the point")

class RouteRequest(BaseModel):
    """Request model for routing between points."""
    origin: Coordinate
    destination: Coordinate
    waypoints: Optional[List[Coordinate]] = Field(None, description="Optional intermediate waypoints")

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
