from fastapi import FastAPI, HTTPException
from typing import Any, Dict
from app.models.schemas import (
    RouteRequest, MatchRequest, MatrixRequest, MatrixGraphResponse, TripRequest,
    VrpRequest, VrpResponse, VrpAllocationResponse
)
from app.services.osrm_client import OSRMClient
from app.services.vrp_service import VrpService
from app.services.graph_builder import GraphBuilder
from app.config import settings

app = FastAPI(title=settings.APP_NAME)
osrm_client = OSRMClient()
vrp_service = VrpService(osrm_client)

@app.get("/health", tags=["System"], summary="Health Check")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": settings.APP_NAME}

@app.post("/route", tags=["Routing"], summary="Calculate Route")
async def get_route(request: RouteRequest):
    """Calculate highly accurate driving route."""
    try:
        # Collect all points: origin, then waypoints (if any), then destination
        points = [request.origin.model_dump()]
        if request.waypoints:
            points.extend([w.model_dump() for w in request.waypoints])
        points.append(request.destination.model_dump())
        
        return await osrm_client.get_route(points, alternatives=request.alternatives)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/matrix", tags=["Matrix"], summary="Get Distance/Duration Matrix")
async def get_matrix(request: MatrixRequest):
    """Fetch raw distance/duration matrix."""
    try:
        return await osrm_client.get_matrix(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/matrix-graph", tags=["Matrix"], summary="Get Matrix as Graph")
async def get_matrix_graph(request: MatrixRequest):
    """Generate a directed graph from a distance/duration matrix."""
    try:
        matrix_data = await osrm_client.get_matrix(request)
        return GraphBuilder.build_from_matrix(matrix_data, request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/match", tags=["Map Matching"], summary="Map Match GPS Traces")
async def match_trace(request: MatchRequest):
    """
    Map match noisy GPS traces to the road network.
    Handles multiple segments automatically (Trace Splitting).
    """
    try:
        # The OSRM /match response naturally contains 'matchings' as a list.
        # If signal is lost, OSRM returns multiple separate matching objects.
        return await osrm_client.match_trace(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/trip", tags=["Optimization"], summary="Solve Traveling Salesperson Problem")
async def get_trip(request: TripRequest):
    """
    Solve TSP for an optimized sequence of coordinates.
    Ideal for comparing actual vs optimized routes.
    """
    try:
        return await osrm_client.get_trip(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/vrp", tags=["Optimization"], summary="Solve Vehicle Routing Problem", response_model=VrpResponse)
async def solve_vrp(request: VrpRequest):
    """
    Solve multi-vehicle Vehicle Routing Problem using Location-Allocation.
    Assigns stops to the logistically closest warehouse before routing.
    """
    try:
        return await vrp_service.solve_vrp(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/vrp/allocate", tags=["Optimization"], summary="Cluster Products to Warehouses", response_model=VrpAllocationResponse)
async def allocate_vrp(request: VrpRequest):
    """
    Cluster products to warehouses using Location-Allocation.
    This is the first phase of the VRP solving process.
    Useful for seeing product-to-depot assignments before routing.
    """
    try:
        return await vrp_service.allocate_products(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
