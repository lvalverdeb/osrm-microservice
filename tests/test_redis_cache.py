import pytest
from typing import Dict, Any
from app.services.redis_cache import RedisCache
from app.services.cache import build_cache_key


class TestRedisCacheUnit:
    def test_init_no_url(self):
        cache = RedisCache(url="", ttl=900, maxsize=1024)
        assert cache.available is False

    def test_init_with_url(self):
        cache = RedisCache(url="redis://localhost:6379/0", ttl=900, maxsize=1024)
        assert cache.available is True

    def test_build_cache_key(self):
        key1 = build_cache_key("/route/v1/driving/...", {"overview": "full"})
        key2 = build_cache_key("/route/v1/driving/...", {"overview": "full"})
        assert key1 == key2

    def test_build_cache_key_different_params(self):
        key1 = build_cache_key("/route/v1/driving/...", {"overview": "full"})
        key2 = build_cache_key("/route/v1/driving/...", {"overview": "simplified"})
        assert key1 != key2

    async def test_get_set_without_redis(self):
        cache = RedisCache(url="", ttl=900, maxsize=1024)
        result = await cache.get("some_key")
        assert result is None

    async def test_clear_without_redis(self):
        cache = RedisCache(url="", ttl=900, maxsize=1024)
        await cache.clear()

    async def test_close_without_redis(self):
        cache = RedisCache(url="", ttl=900, maxsize=1024)
        await cache.close()
        assert cache.available is False


@pytest.mark.asyncio
async def test_redis_cache_graceful_fallback():
    from app.services.osrm_client import OSRMClient
    from unittest.mock import AsyncMock, patch

    client = OSRMClient()
    client.redis_cache = RedisCache(url="redis://nonexistent:6379/0", ttl=900, maxsize=1024)

    with patch.object(client, '_fetch_with_retry', new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"code": "Ok"}
        result = await client._get("/test", {"a": 1})
        assert result == {"code": "Ok"}
        mock_fetch.assert_called_once_with("/test", {"a": 1})


@pytest.mark.asyncio
async def test_main_app_starts_without_redis():
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
