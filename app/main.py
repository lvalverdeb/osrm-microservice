from fastapi import FastAPI, HTTPException, Request, Response
import httpx
import logging
from typing import Any, Dict
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.models.schemas import (
    RouteRequest, MatchRequest, MatrixRequest, MatrixGraphResponse, TripRequest,
    NearestRequest, VrpRequest, VrpResponse, VrpAllocationResponse
)
from app.services.osrm_client import OSRMClient
from app.services.vrp_service import VrpService
from app.services.graph_builder import GraphBuilder
from app.config import settings

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title=settings.APP_NAME)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
osrm_client = OSRMClient()
vrp_service = VrpService(osrm_client)
def _parse_osrm_error(e: httpx.HTTPStatusError):
    """Extract structured error detail from an OSRM error response."""
    try:
        body = e.response.json()
        return {"code": body.get("code", "Error"), "message": body.get("message", "Routing service error")}
    except Exception:
        return "Routing service error"

@app.get("/health", tags=["System"], summary="Health Check")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": settings.APP_NAME}

@app.post("/route", tags=["Routing"], summary="Calculate Route")
@limiter.limit(settings.RATE_LIMIT_ROUTE)
async def get_route(request: Request, payload: RouteRequest):
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
async def get_matrix(request: Request, payload: MatrixRequest):
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
async def get_matrix_graph(request: Request, payload: MatrixRequest):
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
async def match_trace(request: Request, payload: MatchRequest):
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
async def get_trip(request: Request, payload: TripRequest):
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
async def get_nearest(request: Request, payload: NearestRequest):
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
@limiter.limit("600/minute")
async def get_tile(request: Request, profile: str, z: int, x: int, y: int):
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
async def solve_vrp(request: Request, payload: VrpRequest):
    """
    Solve multi-vehicle Vehicle Routing Problem using Location-Allocation.
    Assigns stops to the logistically closest warehouse before routing.
    """
    try:
        return await vrp_service.solve_vrp(payload)
    except Exception:
        logger.exception("Unexpected error on /vrp")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/vrp/allocate", tags=["Optimization"], summary="Cluster Products to Warehouses", response_model=VrpAllocationResponse)
@limiter.limit(settings.RATE_LIMIT_VRP)
async def allocate_vrp(request: Request, payload: VrpRequest):
    """
    Cluster products to warehouses using Location-Allocation.
    This is the first phase of the VRP solving process.
    Useful for seeing product-to-depot assignments before routing.
    """
    try:
        return await vrp_service.allocate_products(payload)
    except Exception:
        logger.exception("Unexpected error on /vrp/allocate")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)