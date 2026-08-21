import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.logging_config import setup_logging
from app.metrics import setup_metrics
from app.models.schemas import (
    MatchRequest,
    MatrixRequest,
    NearestRequest,
    RouteRequest,
    TripRequest,
    VrpAllocationResponse,
    VrpRequest,
    VrpResponse,
)
from app.services.graph_builder import GraphBuilder
from app.services.osrm_client import OSRMClient
from app.services.redis_cache import RedisCache
from app.services.vrp_service import VrpService
from app.tracing import setup_tracing

setup_logging(settings.DEBUG)

logger = logging.getLogger(__name__)

# Limits live in Redis when it is configured: slowapi's default in-memory
# storage is per process, so under `--workers N` -- or across nodes -- the
# effective limit would silently become N x the configured value. The in-memory
# fallback keeps the gateway serving if Redis goes away, matching how
# RedisCache degrades rather than failing.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL or None,
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)
osrm_client = OSRMClient()
redis_cache = RedisCache(url=settings.REDIS_URL, ttl=settings.REDIS_TTL, maxsize=settings.REDIS_MAXSIZE)
osrm_client.redis_cache = redis_cache
vrp_service = VrpService(osrm_client)

# The schema cap bounds one request; this bounds how many run at once, because
# peak memory is the product of the two. A 2000-stop solve peaks near 277 MB, so
# concurrent large solves are what actually exhausts a 2 GB host. Admission
# control belongs next to the rate limiter for the same reason: both decide
# whether a request may proceed, and both are per process -- node-wide
# concurrency is WORKERS x VRP_MAX_CONCURRENCY.
_vrp_slots = asyncio.Semaphore(settings.VRP_MAX_CONCURRENCY)


@asynccontextmanager
async def _vrp_slot() -> AsyncIterator[None]:
    """Hold one bounded solve slot for the duration of the block.

    Waits up to `VRP_QUEUE_TIMEOUT` seconds for a slot so that a burst queues
    instead of multiplying peak memory, and sheds the request rather than
    risking an out-of-memory kill once the wait is exhausted.

    Yields:
        None: once a slot has been acquired.

    Raises:
        HTTPException: 503 with `Retry-After` when no slot frees up in time.
    """
    try:
        async with asyncio.timeout(settings.VRP_QUEUE_TIMEOUT):
            await _vrp_slots.acquire()
    except TimeoutError:
        logger.warning(
            "No VRP slot after %.1fs (limit %d per worker); shedding request",
            settings.VRP_QUEUE_TIMEOUT,
            settings.VRP_MAX_CONCURRENCY,
        )
        raise HTTPException(
            status_code=503,
            detail="Optimization capacity exhausted, retry shortly",
            headers={"Retry-After": str(max(1, int(settings.VRP_QUEUE_TIMEOUT)))},
        )
    try:
        yield
    finally:
        _vrp_slots.release()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await osrm_client.close()
    await redis_cache.close()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
setup_metrics(app)
setup_tracing(app)
def _parse_osrm_error(e: httpx.HTTPStatusError):
    """Extract structured error detail from an OSRM error response."""
    try:
        body = e.response.json()
        return {"code": body.get("code", "Error"), "message": body.get("message", "Routing service error")}
    except Exception as parse_err:
        logger.debug("Failed to parse OSRM error body: %s", parse_err)
        return "Routing service error"

@app.get("/health", tags=["System"], summary="Health Check")
async def health_check() -> dict[str, str]:
    """Health check endpoint that also probes the OSRM backend."""
    osrm_healthy = await osrm_client.ping()

    return {
        "status": "healthy" if osrm_healthy else "degraded",
        "service": settings.APP_NAME,
        "osrm_backend": "up" if osrm_healthy else "down"
    }

@app.get("/ready", tags=["System"], summary="Readiness Probe")
async def readiness_check(response: Response) -> dict[str, str]:
    """Readiness probe for load balancers and deploy-time checks.

    `/health` always answers 200 so humans and dashboards can read the detail,
    which means a balancer keeps sending traffic to a node whose engine is
    down. This endpoint answers 503 in that case, so the node drains instead.

    Args:
        response: Injected response whose status is set to 503 when not ready.

    Returns:
        A mapping with the readiness status and the OSRM backend state.
    """
    osrm_healthy = await osrm_client.ping()
    if not osrm_healthy:
        response.status_code = 503

    return {
        "status": "ready" if osrm_healthy else "not_ready",
        "service": settings.APP_NAME,
        "osrm_backend": "up" if osrm_healthy else "down"
    }

@app.post("/route", tags=["Routing"], summary="Calculate Route")
@limiter.limit(settings.RATE_LIMIT_ROUTE)
async def get_route(request: Request, payload: RouteRequest) -> dict[str, Any]:
    """Calculate highly accurate driving route."""
    try:
        # Collect all points: origin, then waypoints (if any), then destination
        points = [payload.origin.model_dump()]
        if payload.waypoints:
            points.extend([w.model_dump() for w in payload.waypoints])
        points.append(payload.destination.model_dump())
        
        return await osrm_client.get_route(points, request=payload)
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /route: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
    except Exception:
        logger.exception("Unexpected error on /route")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/matrix", tags=["Matrix"], summary="Get Distance/Duration Matrix")
@limiter.limit(settings.RATE_LIMIT_MATRIX)
async def get_matrix(request: Request, payload: MatrixRequest) -> dict[str, Any]:
    """Fetch raw distance/duration matrix."""
    try:
        return await osrm_client.get_matrix(payload)
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /matrix: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
    except Exception:
        logger.exception("Unexpected error on /matrix")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/matrix-graph", tags=["Matrix"], summary="Get Matrix as Graph")
@limiter.limit(settings.RATE_LIMIT_MATRIX)
async def get_matrix_graph(request: Request, payload: MatrixRequest) -> dict[str, Any]:
    """Generate a directed graph from a distance/duration matrix."""
    try:
        matrix_data = await osrm_client.get_matrix(payload)
        return GraphBuilder.build_from_matrix(matrix_data, payload)
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /matrix-graph: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
    except Exception:
        logger.exception("Unexpected error on /matrix-graph")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/match", tags=["Map Matching"], summary="Map Match GPS Traces")
@limiter.limit(settings.RATE_LIMIT_MATCH)
async def match_trace(request: Request, payload: MatchRequest) -> dict[str, Any]:
    """
    Map match noisy GPS traces to the road network.
    Handles multiple segments automatically (Trace Splitting).
    """
    try:
        # The OSRM /match response naturally contains 'matchings' as a list.
        # If signal is lost, OSRM returns multiple separate matching objects.
        return await osrm_client.match_trace(payload)
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /match: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
    except Exception:
        logger.exception("Unexpected error on /match")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/trip", tags=["Optimization"], summary="Solve Traveling Salesperson Problem")
@limiter.limit(settings.RATE_LIMIT_TRIP)
async def get_trip(request: Request, payload: TripRequest) -> dict[str, Any]:
    """
    Solve TSP for an optimized sequence of coordinates.
    Ideal for comparing actual vs optimized routes.
    """
    try:
        return await osrm_client.get_trip(payload)
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /trip: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
    except Exception:
        logger.exception("Unexpected error on /trip")
        raise HTTPException(status_code=500, detail="Internal server error")
@app.post("/nearest", tags=["Routing"], summary="Snap Coordinate to Road Network")
@limiter.limit(settings.RATE_LIMIT_NEAREST)
async def get_nearest(request: Request, payload: NearestRequest) -> dict[str, Any]:
    """
    Find the nearest road network node(s) to a given coordinate.
    Useful for snapping raw GPS coordinates to the routable road graph.
    """
    try:
        return await osrm_client.get_nearest(payload)
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /nearest: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
    except Exception:
        logger.exception("Unexpected error on /nearest")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get(
    "/tile/{profile}/{z}/{x}/{y}.mvt",
    tags=["Tiles"],
    summary="Proxy OSRM Vector Tile",
    response_class=Response
)
@limiter.limit(settings.RATE_LIMIT_TILE)
async def get_tile(request: Request, profile: str, z: int, x: int, y: int) -> Response:
    """
    Proxy a Mapbox Vector Tile from the OSRM tile service.
    Returns binary protobuf content (application/x-protobuf).
    Minimum zoom level supported by OSRM is 12.
    """
    try:
        tile_bytes = await osrm_client.get_tile(profile, z, x, y)
        return Response(content=tile_bytes, media_type="application/x-protobuf")
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /tile: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
    except Exception:
        logger.exception("Unexpected error on /tile")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/vrp", tags=["Optimization"], summary="Solve Vehicle Routing Problem", response_model=VrpResponse)
@limiter.limit(settings.RATE_LIMIT_VRP)
async def solve_vrp(request: Request, payload: VrpRequest) -> VrpResponse:
    """
    Solve multi-vehicle Vehicle Routing Problem using Location-Allocation.
    Assigns stops to the logistically closest warehouse before routing.

    Args:
        request: Incoming request, used by the rate limiter.
        payload: Depots, stops, and clustering options for the solve.

    Returns:
        VrpResponse: One optimized route per vehicle.

    Raises:
        HTTPException: 503 when solve capacity is exhausted, 500 on failure.
    """
    async with _vrp_slot():
        try:
            return await vrp_service.solve_vrp(payload)
        except Exception:
            logger.exception("Unexpected error on /vrp")
            raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/vrp/allocate", tags=["Optimization"], summary="Cluster Products to Warehouses", response_model=VrpAllocationResponse)
@limiter.limit(settings.RATE_LIMIT_VRP)
async def allocate_vrp(request: Request, payload: VrpRequest) -> VrpAllocationResponse:
    """
    Cluster products to warehouses using Location-Allocation.
    This is the first phase of the VRP solving process.
    Useful for seeing product-to-depot assignments before routing.

    Args:
        request: Incoming request, used by the rate limiter.
        payload: Depots, stops, and clustering options for the allocation.

    Returns:
        VrpAllocationResponse: Depot-to-stop assignments and unreachable stops.

    Raises:
        HTTPException: 503 when solve capacity is exhausted, 500 on failure.
    """
    async with _vrp_slot():
        try:
            return await vrp_service.allocate_products(payload)
        except Exception:
            logger.exception("Unexpected error on /vrp/allocate")
            raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)