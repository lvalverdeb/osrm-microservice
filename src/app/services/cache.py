from cachetools import TTLCache
from typing import Any, Dict
from app.config import settings

response_cache: TTLCache[str, Dict[str, Any]] = TTLCache(
    maxsize=settings.L1_CACHE_MAXSIZE, ttl=settings.L1_CACHE_TTL
)


def build_cache_key(endpoint: str, params: Dict[str, Any] = None) -> str:
    return f"{endpoint}:{hash(frozenset(sorted((params or {}).items())))}"
