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

    def test_build_cache_key_stable_across_interpreters(self):
        # Regression test: the key must not depend on the builtin hash()'s
        # per-process PYTHONHASHSEED salt, since it's shared with the L2 Redis
        # cache across replicas/restarts. Spawn a fresh interpreter with a
        # different hash seed and confirm it produces the identical key.
        import subprocess
        import sys

        script = (
            "from app.services.cache import build_cache_key;"
            "print(build_cache_key('/route/v1/driving/...', {'overview': 'full'}))"
        )
        import os

        key_local = build_cache_key("/route/v1/driving/...", {"overview": "full"})
        env = {**os.environ, "PYTHONHASHSEED": "random"}
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == key_local

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
