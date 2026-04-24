import asyncio
import sys
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.append('.')
sys.path.append('./.venv/lib/python3.12/site-packages')

from app.services.osrm_client import OSRMClient
from app.main import _parse_osrm_error

async def test_tile_proxy():
    print("Testing Tile proxy...", end=" ")
    client = OSRMClient()
    with patch.object(client._client, 'get', new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_error = False
        mock_response.content = b"fake-tile-data"
        mock_get.return_value = mock_response
        
        tile_bytes = await client.get_tile("driving", 12, 100, 200)
        
        args, _ = mock_get.call_args
        assert "/tile/v1/driving/tile(100,200,12).mvt" in args[0]
        assert tile_bytes == b"fake-tile-data"
    await client.close()
    print("PASSED")

def test_error_parsing():
    print("Testing Error parsing...", end=" ")
    mock_response = MagicMock()
    mock_response.json.return_value = {"code": "NoRoute", "message": "Could not find a route"}
    
    e = httpx.HTTPStatusError("error", request=MagicMock(), response=mock_response)
    result = _parse_osrm_error(e)
    
    assert result["code"] == "NoRoute"
    assert result["message"] == "Could not find a route"
    print("PASSED")

async def main():
    try:
        await test_tile_proxy()
        test_error_parsing()
        print("\nPhase 4 Tile and Error tests PASSED.")
    except AssertionError as e:
        print(f"\nPHASE 4 TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
