import httpx
import logging
from typing import Any, Dict, List
import tenacity
from app.config import settings
from app.models.schemas import RouteRequest, MatchRequest, MatrixRequest, TripRequest, NearestRequest
from app.services.cache import response_cache, build_cache_key

logger = logging.getLogger(__name__)


class OSRMClient:
    """Async client for interacting with the OSRM API with connection pooling."""

    def __init__(self):
        self.base_url = settings.OSRM_BASE_URL
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=settings.OSRM_CLIENT_TIMEOUT
        )

    # spaghetti-ignore[pass-through-method]: lifecycle wrapper; exposing the raw httpx client would leak transport internals
    async def close(self) -> None:
        """Gracefully close the underlying httpx client."""
        await self._client.aclose()

    async def ping(self) -> bool:
        """Probe the OSRM backend with a minimal route request.

        Returns:
            True if OSRM responded without an error status, False otherwise.
        """
        try:
            response = await self._client.get(
                f"/route/v1/driving/{settings.HEALTH_CHECK_COORDS}",
                timeout=settings.HEALTH_CHECK_TIMEOUT,
            )
            return not response.is_error
        except Exception as probe_err:
            logger.warning("OSRM health probe failed: %s", probe_err)
            return False

    @staticmethod
    def _retryable(exception: Exception) -> bool:
        if isinstance(exception, httpx.HTTPStatusError):
            return exception.response.status_code >= 500
        return isinstance(exception, (httpx.TimeoutException, httpx.TransportError))

    async def _get(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Internal helper with L1→L2→OSRM cache-aside strategy and retry."""
        cache_key = build_cache_key(endpoint, params)

        # L1: in-memory cache
        cached = response_cache.get(cache_key)
        if cached is not None:
            logger.debug("L1 hit for %s", endpoint)
            return cached

        # L2: Redis cache
        redis_cache = getattr(self, 'redis_cache', None)
        if redis_cache is not None and redis_cache.available:
            cached = await redis_cache.get(cache_key)
            if cached is not None:
                logger.debug("L2 hit for %s", endpoint)
                response_cache[cache_key] = cached
                return cached

        # Miss — fetch from OSRM
        data = await self._fetch_with_retry(endpoint, params)

        # Populate both layers
        response_cache[cache_key] = data
        if redis_cache is not None and redis_cache.available:
            await redis_cache.set(cache_key, data)

        return data

    @tenacity.retry(
        retry=tenacity.retry_if_exception(_retryable),
        wait=tenacity.wait_exponential(
            multiplier=1,
            min=settings.OSRM_RETRY_MIN,
            max=settings.OSRM_RETRY_MAX,
        ),
        stop=tenacity.stop_after_attempt(settings.OSRM_RETRY_ATTEMPTS),
    )
    async def _fetch_with_retry(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Fetch from OSRM with exponential backoff retry for transient failures."""
        response = await self._client.get(endpoint, params=params)
        if response.is_error:
            logger.error("OSRM API error at %s: status=%s", response.url, response.status_code)
        response.raise_for_status()
        return response.json()

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

    _COMMON_OPTION_SERIALIZERS = [
        ("bearings", lambda values: ";".join(v if v is not None else "" for v in values)),
        ("radiuses", lambda values: ";".join(str(v) if v is not None else "unlimited" for v in values)),
        ("hints", lambda values: ";".join(v if v is not None else "" for v in values)),
        ("approaches", lambda values: ";".join(v if v is not None else "" for v in values)),
        ("exclude", lambda values: ",".join(values)),
        ("snapping", lambda value: value),
        ("skip_waypoints", lambda value: "true" if value else "false"),
    ]

    @staticmethod
    def _serialize_common_options(request) -> Dict[str, Any]:
        """Serialize optional OSRM general options into query parameter entries."""
        params: Dict[str, Any] = {}
        for field, serialize in OSRMClient._COMMON_OPTION_SERIALIZERS:
            value = getattr(request, field)
            if value is not None:
                params[field] = serialize(value)
        return params

    async def get_tile(self, profile: str, z: int, x: int, y: int) -> bytes:
        """Fetch a Mapbox Vector Tile from OSRM and return raw bytes."""
        endpoint = f"/tile/v1/{profile}/tile({x},{y},{z}).mvt"
        response = await self._client.get(endpoint)
        if response.is_error:
            logger.error("OSRM tile error at %s: status=%s", response.url, response.status_code)
        response.raise_for_status()
        return response.content
