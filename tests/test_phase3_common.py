import asyncio
import sys
from unittest.mock import AsyncMock, patch

sys.path.append('.')
sys.path.append('./.venv/lib/python3.12/site-packages')

from app.services.osrm_client import OSRMClient
from app.models.schemas import RouteRequest, Coordinate

async def test_common_options_serialization():
    print("Testing Common Options serialization...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok"}
        coords = [{"longitude": -84.0, "latitude": 9.0}, {"longitude": -84.1, "latitude": 9.1}]
        req = RouteRequest(
            origin=Coordinate(longitude=-84.0, latitude=9.0),
            destination=Coordinate(longitude=-84.1, latitude=9.1),
            bearings=[ "90,30", None ],
            radiuses=[ 50, None ],
            exclude=["motorway", "toll"],
            skip_waypoints=True
        )
        await client.get_route(coords, request=req)
        
        params = mock_get.call_args.kwargs.get('params')
        print(f"\nGenerated params: {params}")
        
        assert params["bearings"] == "90,30;"
        assert params["radiuses"] == "50.0;unlimited"
        assert params["exclude"] == "motorway,toll"
        assert params["skip_waypoints"] == "true"
    await client.close()
    print("PASSED")

async def main():
    try:
        await test_common_options_serialization()
        print("\nPhase 3 Common Options tests PASSED.")
    except AssertionError as e:
        print(f"\nPHASE 3 TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
