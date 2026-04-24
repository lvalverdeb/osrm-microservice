import asyncio
import sys
from unittest.mock import AsyncMock, patch

sys.path.append('.')
sys.path.append('./.venv/lib/python3.12/site-packages')

from app.services.osrm_client import OSRMClient
from app.models.schemas import NearestRequest, Coordinate

async def test_nearest_service():
    print("Testing Nearest service...", end=" ")
    client = OSRMClient()
    with patch.object(client, '_get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"code": "Ok", "waypoints": []}
        req = NearestRequest(
            coordinate=Coordinate(longitude=-84.0, latitude=9.0),
            number=3,
            profile="cycling"
        )
        await client.get_nearest(req)
        
        args, _ = mock_get.call_args
        params = mock_get.call_args.kwargs.get('params')
        
        assert "/nearest/v1/cycling/-84.0,9.0" in args[0]
        assert params["number"] == 3
    await client.close()
    print("PASSED")

async def main():
    try:
        await test_nearest_service()
        print("\nPhase 2 Nearest service tests PASSED.")
    except AssertionError as e:
        print(f"\nPHASE 2 TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
