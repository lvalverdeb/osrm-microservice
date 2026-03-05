import httpx
import logging
from typing import Any, Dict, List
from app.config import settings
from app.models.schemas import MatchRequest, MatrixRequest

logger = logging.getLogger(__name__)

class OSRMClient:
    """Async client for interacting with the OSRM API."""

    def __init__(self):
        self.base_url = settings.OSRM_BASE_URL

    async def _get(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Internal helper for GET requests."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}{endpoint}"
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"OSRM API error at {url}: {e}")
                raise

    async def get_route(self, coordinates: List[Dict[str, float]]) -> Dict[str, Any]:
        """Fetch a driving route passing through multiple points."""
        coords_str = ";".join([f"{c['longitude']},{c['latitude']}" for c in coordinates])
        # By default all points are waypoints in /route, but we explicit it
        waypoints_indices = ";".join([str(i) for i in range(len(coordinates))])
        return await self._get(f"/route/v1/driving/{coords_str}", params={
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "annotations": "distance,duration",
            "waypoints": waypoints_indices
        })

    async def get_matrix(self, request: MatrixRequest) -> Dict[str, Any]:
        """Fetch a duration/distance matrix for multiple points."""
        coords = ";".join([f"{c.longitude},{c.latitude}" for c in request.coordinates])
        params = {"annotations": "duration,distance"}
        if request.sources:
            params["sources"] = ";".join(map(str, request.sources))
        if request.destinations:
            params["destinations"] = ";".join(map(str, request.destinations))
        return await self._get(f"/table/v1/driving/{coords}", params=params)

    async def match_trace(self, request: MatchRequest) -> Dict[str, Any]:
        """Clean up noisy GPS breadcrumbs via map matching."""
        coords = ";".join([f"{b.longitude},{b.latitude}" for b in request.breadcrumbs])
        timestamps = ";".join([str(b.timestamp) for b in request.breadcrumbs])
        radiuses = ";".join([str(b.accuracy_meters) for b in request.breadcrumbs])
        
        return await self._get(f"/match/v1/driving/{coords}", params={
            "timestamps": timestamps,
            "radiuses": radiuses,
            "overview": "full",
            "geometries": "geojson"
        })
