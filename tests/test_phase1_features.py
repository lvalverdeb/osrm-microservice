import asyncio
import sys
from unittest.mock import AsyncMock, patch

sys.path.append('.')
sys.path.append('./.venv/lib/python3.12/site-packages')

from app.services.osrm_client import OSRMClient
from app.models.schemas import RouteRequest, MatrixRequest, MatchRequest, TripRequest, Coordinate

async def test_route_walking_profile():
    print("Testing Route walking profile...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        coords = [{"longitude": -84.0, "latitude": 9.0}, {"longitude": -84.1, "latitude": 9.1}]
        req = RouteRequest(
            origin=Coordinate(longitude=-84.0, latitude=9.0),
            destination=Coordinate(longitude=-84.1, latitude=9.1),
            profile="walking",
            overview="simplified",
            steps=False
        )
        await client.get_route(coords, request=req)
        
        args, _ = mock_get.call_args
        params = mock_get.call_args.kwargs.get('params')
        
        assert "/route/v1/walking/" in args[0]
        assert params["overview"] == "simplified"
        assert params["steps"] == "false"
    await client.close()
    print("PASSED")

async def test_matrix_cycling_profile():
    print("Testing Matrix cycling profile...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        req = MatrixRequest(
            coordinates=[Coordinate(longitude=-84.0, latitude=9.0), Coordinate(longitude=-84.1, latitude=9.1)],
            profile="cycling",
            fallback_speed=15.5
        )
        await client.get_matrix(req)
        
        args, _ = mock_get.call_args
        params = mock_get.call_args.kwargs.get('params')
        
        assert "/table/v1/cycling/" in args[0]
        assert params["fallback_speed"] == 15.5
    await client.close()
    print("PASSED")

async def test_match_steps_and_tidy():
    print("Testing Match steps and tidy...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        # req defined with steps=True, tidy=True
        from app.models.schemas import GPSBreadcrumb
        req = MatchRequest(
            breadcrumbs=[
                GPSBreadcrumb(longitude=-84.0, latitude=9.0, timestamp=1000),
                GPSBreadcrumb(longitude=-84.1, latitude=9.1, timestamp=2000)
            ],
            steps=True,
            tidy=True
        )
        await client.match_trace(req)
        
        params = mock_get.call_args.kwargs.get('params')
        assert params["steps"] == "true"
        assert params["tidy"] == "true"
    await client.close()
    print("PASSED")

async def main():
    try:
        await test_route_walking_profile()
        await test_matrix_cycling_profile()
        await test_match_steps_and_tidy()
        print("\nAll Phase 1 new feature tests PASSED.")
    except AssertionError as e:
        print(f"\nPHASE 1 FEATURE TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
