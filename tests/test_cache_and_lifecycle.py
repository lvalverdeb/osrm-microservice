from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import tenacity

from app.services.cache import response_cache
from app.services.osrm_client import OSRMClient


def _mock_response(status_code: int, json_data: dict | None = None, is_error: bool = False):
    mock = Mock(spec=httpx.Response)
    mock.status_code = status_code
    mock.is_error = is_error
    if is_error:
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=Mock(),
            response=mock,
        )
    else:
        mock.raise_for_status.return_value = None
    mock.json.return_value = json_data or {}
    return mock


@pytest.mark.asyncio
async def test_get_caches_response():
    client = OSRMClient()
    response_cache.clear()
    mock_data = {"code": "Ok", "routes": []}
    with patch.object(client, "_fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_data
        result1 = await client._get("/test", {"a": "1"})
        result2 = await client._get("/test", {"a": "1"})
    assert result1 == mock_data
    assert result2 == mock_data
    assert mock_fetch.call_count == 1
    await client.close()


@pytest.mark.asyncio
async def test_get_skips_cache_for_different_params():
    client = OSRMClient()
    response_cache.clear()
    mock_data = {"code": "Ok"}
    with patch.object(client, "_fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_data
        await client._get("/test", {"a": "1"})
        await client._get("/test", {"a": "2"})
    assert mock_fetch.call_count == 2
    await client.close()


@pytest.mark.asyncio
async def test_retry_does_not_retry_4xx():
    client = OSRMClient()
    mock_resp = _mock_response(404, is_error=True)
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
        with pytest.raises(httpx.HTTPStatusError):
            await client._fetch_with_retry("/test", {})
        assert mock_get.call_count == 1
    await client.close()


@pytest.mark.asyncio
async def test_retry_retries_5xx():
    client = OSRMClient()
    mock_resp = _mock_response(503, is_error=True)
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
        with pytest.raises(tenacity.RetryError):
            await client._fetch_with_retry("/test", {})
        assert mock_get.call_count == 3
    await client.close()


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure():
    client = OSRMClient()
    call_count = 0
    async def mock_get_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return _mock_response(503, is_error=True)
        return _mock_response(200, json_data={"code": "Ok"})
    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = mock_get_side_effect
        result = await client._fetch_with_retry("/test", {})
    assert result["code"] == "Ok"
    assert call_count == 3
    await client.close()


@pytest.mark.asyncio
async def test_close_cleans_up_client():
    client = OSRMClient()
    with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_aclose:
        await client.close()
    mock_aclose.assert_awaited_once()
