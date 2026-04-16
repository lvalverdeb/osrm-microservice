import httpx
import logging
from typing import Any, Dict, List, Union
from app.config import settings
from app.models.schemas import MatchRequest, MatrixRequest, TripRequest

logger = logging.getLogger(__name__)


class OSRMClient:
    """Async client for interacting with the OSRM API with connection pooling."""

    def __init__(self):
        self.base_url = settings.OSRM_BASE_URL
        # Initialize the client once so it maintains a connection pool
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def close(self):
        """Gracefully close the underlying httpx client."""
        await self._client.aclose()

    async def _get(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Internal helper for GET requests using the shared client pool."""
        response = None
        try:
            response = await self._client.get(endpoint, params=params)

            if response.is_error:
                logger.error("OSRM API error at %s: status=%s", response.url, response.status_code)
            response.raise_for_status()

            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error("HTTPStatusError at %s: status=%s", e.request.url, e.response.status_code)
            raise
        except httpx.HTTPError as e:
            url = getattr(getattr(e, 'request', None), 'url', endpoint)
            logger.error("OSRM API connection error at %s: %s", url, type(e).__name__)
            raise

    async def get_route(self, coordinates: List[Dict[str, float]], alternatives: Union[bool, int] = False) -> Dict[
        str, Any]:
        """Fetch a driving route passing through multiple points."""
        coords_str = ";".join([f"{c['longitude']},{c['latitude']}" for c in coordinates])
        waypoints_indices = ";".join([str(i) for i in range(len(coordinates))])
        alt_param = "true" if alternatives is True else "false" if alternatives is False else str(alternatives)

        return await self._get(f"/route/v1/driving/{coords_str}", params={
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "annotations": "distance,duration",
            "waypoints": waypoints_indices,
            "alternatives": alt_param
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

    async def get_trip(self, request: TripRequest) -> Dict[str, Any]:
        """Solve TSP for an optimized sequence of coordinates."""
        coords = ";".join([f"{c.longitude},{c.latitude}" for c in request.coordinates])
        return await self._get(f"/trip/v1/driving/{coords}", params={
            "source": request.source,
            "destination": request.destination,
            "roundtrip": "true" if request.roundtrip else "false",
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "annotations": "distance,duration"
        })
