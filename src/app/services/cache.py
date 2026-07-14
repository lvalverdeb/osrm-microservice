import hashlib
import json
from cachetools import TTLCache
from typing import Any, Dict
from app.config import settings

response_cache: TTLCache[str, Dict[str, Any]] = TTLCache(
    maxsize=settings.L1_CACHE_MAXSIZE, ttl=settings.L1_CACHE_TTL
)


def build_cache_key(endpoint: str, params: Dict[str, Any] = None) -> str:
    # json.dumps + sha256 (not the builtin hash(), which is salted per-process)
    # so the key is stable across replicas and restarts sharing the L2 Redis cache.
    serialized = json.dumps(params or {}, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    return f"{endpoint}:{digest}"
