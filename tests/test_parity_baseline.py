import pytest
from unittest.mock import AsyncMock, patch
from app.services.osrm_client import OSRMClient
from app.models.schemas import MatrixRequest, MatchRequest, TripRequest, Coordinate, GPSBreadcrumb

@pytest.mark.asyncio
async def test_route_parity():
    """Verify the current baseline for the Route service."""
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        
        coords = [{"longitude": -84.0, "latitude": 9.0}, {"longitude": -84.1, "latitude": 9.1}]
        # Current signature: get_route(coordinates, alternatives)
        await client.get_route(coords, alternatives=True)
        
        args, kwargs = mock_get.call_args
        endpoint = args[0]
        params = kwargs.get('params')
        
        # Snapshot of current hardcoded behavior
        assert "/route/v1/driving/" in endpoint
        assert params["overview"] == "full"
        assert params["geometries"] == "geojson"
        assert params["steps"] == "true"
        assert params["annotations"] == "distance,duration"
        assert params["alternatives"] == "true"
        assert "waypoints" in params
    await client.close()

@pytest.mark.asyncio
async def test_matrix_parity():
    """Verify the current baseline for the Table (Matrix) service."""
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        
        req = MatrixRequest(coordinates=[
            Coordinate(longitude=-84.0, latitude=9.0), 
            Coordinate(longitude=-84.1, latitude=9.1)
        ])
        await client.get_matrix(req)
        
        args, kwargs = mock_get.call_args
        endpoint = args[0]
        params = kwargs.get('params')
        
        # Snapshot of current hardcoded behavior
        assert "/table/v1/driving/" in endpoint
        assert params["annotations"] == "duration,distance"
    await client.close()

@pytest.mark.asyncio
async def test_match_parity():
    """Verify the current baseline for the Match service."""
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        
        req = MatchRequest(breadcrumbs=[
            GPSBreadcrumb(longitude=-84.0, latitude=9.0, timestamp=1000),
            GPSBreadcrumb(longitude=-84.1, latitude=9.1, timestamp=2000)
        ])
        await client.match_trace(req)
        
        args, kwargs = mock_get.call_args
        endpoint = args[0]
        params = kwargs.get('params')
        
        # Snapshot of current hardcoded behavior
        assert "/match/v1/driving/" in endpoint
        assert params["overview"] == "full"
        assert params["geometries"] == "geojson"
        assert "timestamps" in params
        assert "radiuses" in params
    await client.close()

@pytest.mark.asyncio
async def test_trip_parity():
    """Verify the current baseline for the Trip service."""
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        
        req = TripRequest(coordinates=[
            Coordinate(longitude=-84.0, latitude=9.0), 
            Coordinate(longitude=-84.1, latitude=9.1)
        ])
        await client.get_trip(req)
        
        args, kwargs = mock_get.call_args
        endpoint = args[0]
        params = kwargs.get('params')
        
        # Snapshot of current hardcoded behavior
        assert "/trip/v1/driving/" in endpoint
        assert params["overview"] == "full"
        assert params["geometries"] == "geojson"
        assert params["steps"] == "true"
        assert params["annotations"] == "distance,duration"
        assert params["roundtrip"] == "true"
    await client.close()
