import asyncio
import sys
import logging
from unittest.mock import AsyncMock, patch

# Ensure app is importable
sys.path.append('.')
# Add .venv site-packages for pydantic, etc.
sys.path.append('./.venv/lib/python3.12/site-packages')

from app.services.osrm_client import OSRMClient
from app.models.schemas import RouteRequest, MatrixRequest, MatchRequest, TripRequest, Coordinate, GPSBreadcrumb

# Disable logging for cleaner output
logging.disable(logging.CRITICAL)

async def test_route_parity():
    print("Testing Route parity...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        coords = [{"longitude": -84.0, "latitude": 9.0}, {"longitude": -84.1, "latitude": 9.1}]
        req = RouteRequest(
            origin=Coordinate(longitude=-84.0, latitude=9.0),
            destination=Coordinate(longitude=-84.1, latitude=9.1),
            alternatives=True
        )
        await client.get_route(coords, request=req)
        
        args, _ = mock_get.call_args
        params = mock_get.call_args.kwargs.get('params')
        
        assert "/route/v1/driving/" in args[0]
        assert params["overview"] == "full"
        assert params["geometries"] == "geojson"
        assert params["steps"] == "true"
        assert params["annotations"] == "distance,duration"
        assert params["alternatives"] == "true"
    await client.close()
    print("PASSED")

async def test_matrix_parity():
    print("Testing Matrix parity...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        req = MatrixRequest(coordinates=[
            Coordinate(longitude=-84.0, latitude=9.0), 
            Coordinate(longitude=-84.1, latitude=9.1)
        ])
        await client.get_matrix(req)
        
        args, _ = mock_get.call_args
        params = mock_get.call_args.kwargs.get('params')
        
        assert "/table/v1/driving/" in args[0]
        assert params["annotations"] == "duration,distance"
    await client.close()
    print("PASSED")

async def test_match_parity():
    print("Testing Match parity...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        req = MatchRequest(breadcrumbs=[
            GPSBreadcrumb(longitude=-84.0, latitude=9.0, timestamp=1000),
            GPSBreadcrumb(longitude=-84.1, latitude=9.1, timestamp=2000)
        ])
        await client.match_trace(req)
        
        args, _ = mock_get.call_args
        params = mock_get.call_args.kwargs.get('params')
        
        assert "/match/v1/driving/" in args[0]
        assert params["overview"] == "full"
        assert params["geometries"] == "geojson"
    await client.close()
    print("PASSED")

async def test_trip_parity():
    print("Testing Trip parity...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        req = TripRequest(coordinates=[
            Coordinate(longitude=-84.0, latitude=9.0), 
            Coordinate(longitude=-84.1, latitude=9.1)
        ])
        await client.get_trip(req)
        
        args, _ = mock_get.call_args
        params = mock_get.call_args.kwargs.get('params')
        
        assert "/trip/v1/driving/" in args[0]
        assert params["overview"] == "full"
        assert params["geometries"] == "geojson"
        assert params["steps"] == "true"
        assert params["annotations"] == "distance,duration"
    await client.close()
    print("PASSED")

async def main():
    try:
        await test_route_parity()
        await test_matrix_parity()
        await test_match_parity()
        await test_trip_parity()
        print("\nAll baseline parity tests PASSED.")
    except AssertionError as e:
        print("\nBASELINE PARITY FAILED!")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
