import json
import logging
from typing import Any, Dict, Optional
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisCache:
    def __init__(self, url: str, ttl: int, maxsize: int):
        self._ttl = ttl
        self._maxsize = maxsize
        self._redis: Optional[redis.Redis] = None
        self._available = False
        if url:
            self._redis = redis.from_url(url, decode_responses=False)
            self._available = True
            logger.info("Redis cache configured at %s", url)

    @property
    def available(self) -> bool:
        return self._available

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self._available or self._redis is None:
            return None
        try:
            data = await self._redis.get(key)
            if data is not None:
                # spaghetti-ignore[magic-number]: debug-log key truncation length, not a tunable value
                logger.debug("Redis L2 hit for key %s", key[:40])
                return json.loads(data)
        except Exception as e:
            logger.warning("Redis get failed, falling back: %s", e)
        return None

    async def set(self, key: str, value: Dict[str, Any]) -> None:
        if not self._available or self._redis is None:
            return
        try:
            await self._redis.set(key, json.dumps(value), ex=self._ttl)
        except Exception as e:
            logger.warning("Redis set failed: %s", e)

    async def clear(self) -> None:
        if not self._available or self._redis is None:
            return
        try:
            await self._redis.flushdb()
        except Exception as e:
            logger.warning("Redis clear failed: %s", e)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._available = False
