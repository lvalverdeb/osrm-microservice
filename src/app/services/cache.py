import hashlib
import json
from typing import Any

from cachetools import TTLCache
from prometheus_client import Counter

from app.config import settings

response_cache: TTLCache[str, dict[str, Any]] = TTLCache(
    maxsize=settings.L1_CACHE_MAXSIZE, ttl=settings.L1_CACHE_TTL
)

# L2 is only consulted after an L1 miss, so the l2 series are a subset of the l1
# misses -- do not add the tiers together when computing a hit rate. A tier that
# is not configured (no REDIS_URL) records nothing at all, which is deliberate:
# an absent tier is not a miss.
cache_lookups_total = Counter(
    "cache_lookups_total",
    "Cache-aside lookups by tier and outcome.",
    ["tier", "result", "service"],
)

# The OSRM endpoint carries coordinates (/route/v1/driving/-84.09,9.92;...), so
# it can never be a label value -- every distinct request would mint a new time
# series. Only the leading path segment is bounded, and only these reach _get;
# tiles are not cached. Anything else collapses to "other" rather than widening
# the label space.
_SERVICES = frozenset({"route", "table", "match", "trip", "nearest"})


def _service_label(endpoint: str) -> str:
    """Reduce an OSRM endpoint path to a bounded metric label.

    Args:
        endpoint: OSRM path such as `/route/v1/driving/-84.09,9.92;-84.08,9.93`.

    Returns:
        The leading path segment when it is a known OSRM service, else "other".
    """
    segment = endpoint.lstrip("/").split("/", 1)[0]
    return segment if segment in _SERVICES else "other"


def record_lookup(tier: str, result: str, endpoint: str) -> None:
    """Count one cache lookup.

    Args:
        tier: Cache layer consulted, "l1" or "l2".
        result: Outcome, "hit" or "miss".
        endpoint: OSRM path the lookup was for; reduced to a bounded label.
    """
    cache_lookups_total.labels(tier=tier, result=result, service=_service_label(endpoint)).inc()


def build_cache_key(endpoint: str, params: dict[str, Any] | None = None) -> str:
    # json.dumps + sha256 (not the builtin hash(), which is salted per-process)
    # so the key is stable across replicas and restarts sharing the L2 Redis cache.
    serialized = json.dumps(params or {}, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    return f"{endpoint}:{digest}"
