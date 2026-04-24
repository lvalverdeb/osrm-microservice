import httpx
import logging
from typing import Any, Dict, List, Union
from app.config import settings
from app.models.schemas import RouteRequest, MatchRequest, MatrixRequest, TripRequest, NearestRequest

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

    async def get_route(self, coordinates: List[Dict[str, float]], request: RouteRequest) -> Dict[str, Any]:
        """Fetch a routing result passing through multiple points."""
        coords_str = ";".join([f"{c['longitude']},{c['latitude']}" for c in coordinates])
        waypoints_indices = ";".join([str(i) for i in range(len(coordinates))])
        alt_param = "true" if request.alternatives is True else "false" if request.alternatives is False else str(request.alternatives)

        params = {
            "overview": request.overview,
            "geometries": request.geometries,
            "steps": "true" if request.steps else "false",
            "waypoints": waypoints_indices,
            "alternatives": alt_param
        }
        if request.annotations is not None:
            params["annotations"] = request.annotations
        if request.continue_straight is not None:
            params["continue_straight"] = request.continue_straight

        params.update(self._serialize_common_options(request))

        return await self._get(f"/route/v1/{request.profile}/{coords_str}", params=params)

    async def get_matrix(self, request: MatrixRequest) -> Dict[str, Any]:
        """Fetch a duration/distance matrix for multiple points."""
        coords = ";".join([f"{c.longitude},{c.latitude}" for c in request.coordinates])
        params = {"annotations": request.annotations}
        if request.sources:
            params["sources"] = ";".join(map(str, request.sources))
        if request.destinations:
            params["destinations"] = ";".join(map(str, request.destinations))
        if request.fallback_speed is not None:
            params["fallback_speed"] = request.fallback_speed
        if request.fallback_coordinate is not None:
            params["fallback_coordinate"] = request.fallback_coordinate
        if request.scale_factor is not None:
            params["scale_factor"] = request.scale_factor

        params.update(self._serialize_common_options(request))

        return await self._get(f"/table/v1/{request.profile}/{coords}", params=params)

    async def match_trace(self, request: MatchRequest) -> Dict[str, Any]:
        """Clean up noisy GPS breadcrumbs via map matching."""
        coords = ";".join([f"{b.longitude},{b.latitude}" for b in request.breadcrumbs])
        timestamps = ";".join([str(b.timestamp) for b in request.breadcrumbs])
        radiuses = ";".join([str(b.accuracy_meters) for b in request.breadcrumbs])

        params = {
            "timestamps": timestamps,
            "radiuses": radiuses,
            "overview": request.overview,
            "geometries": request.geometries,
            "steps": "true" if request.steps else "false",
            "tidy": "true" if request.tidy else "false" if request.tidy is not None else None
        }
        if request.annotations is not None:
            params["annotations"] = request.annotations
        if request.gaps is not None:
            params["gaps"] = request.gaps
        if request.match_waypoints is not None:
            params["waypoints"] = ";".join(map(str, request.match_waypoints))

        # Remove None values from params
        params = {k: v for k, v in params.items() if v is not None}
        params.update(self._serialize_common_options(request))

        return await self._get(f"/match/v1/{request.profile}/{coords}", params=params)

    async def get_trip(self, request: TripRequest) -> Dict[str, Any]:
        """Solve TSP for an optimized sequence of coordinates."""
        coords = ";".join([f"{c.longitude},{c.latitude}" for c in request.coordinates])
        params = {
            "source": request.source,
            "destination": request.destination,
            "roundtrip": "true" if request.roundtrip else "false",
            "overview": request.overview,
            "geometries": request.geometries,
            "steps": "true" if request.steps else "false"
        }
        if request.annotations is not None:
            params["annotations"] = request.annotations

        params.update(self._serialize_common_options(request))

        return await self._get(f"/trip/v1/{request.profile}/{coords}", params=params)

    async def get_nearest(self, request: NearestRequest) -> Dict[str, Any]:
        """Find the nearest road network point(s) to a given coordinate."""
        coord_str = f"{request.coordinate.longitude},{request.coordinate.latitude}"
        params = {"number": request.number}
        params.update(self._serialize_common_options(request))
        return await self._get(
            f"/nearest/v1/{request.profile}/{coord_str}",
            params=params
        )

    @staticmethod
    def _serialize_common_options(request) -> Dict[str, Any]:
        """Serialize optional OSRM general options into query parameter entries."""
        params = {}
        if request.bearings is not None:
            params["bearings"] = ";".join(b if b is not None else "" for b in request.bearings)
        if request.radiuses is not None:
            params["radiuses"] = ";".join(str(r) if r is not None else "unlimited" for r in request.radiuses)
        if request.hints is not None:
            params["hints"] = ";".join(h if h is not None else "" for h in request.hints)
        if request.approaches is not None:
            params["approaches"] = ";".join(a if a is not None else "" for a in request.approaches)
        if request.exclude is not None:
            params["exclude"] = ",".join(request.exclude)
        if request.snapping is not None:
            params["snapping"] = request.snapping
        if request.skip_waypoints is not None:
            params["skip_waypoints"] = "true" if request.skip_waypoints else "false"
        return params

    async def get_tile(self, profile: str, z: int, x: int, y: int) -> bytes:
        """Fetch a Mapbox Vector Tile from OSRM and return raw bytes."""
        endpoint = f"/tile/v1/{profile}/tile({x},{y},{z}).mvt"
        response = await self._client.get(endpoint)
        if response.is_error:
            logger.error("OSRM tile error at %s: status=%s", response.url, response.status_code)
        response.raise_for_status()
        return response.content
