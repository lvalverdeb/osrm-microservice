from unittest.mock import AsyncMock, patch

import pytest

from app.services.cache import _service_label, cache_lookups_total, response_cache
from app.services.osrm_client import OSRMClient


def _count(tier: str, result: str, service: str) -> float:
    """Read one counter series, treating an untouched series as zero."""
    return cache_lookups_total.labels(tier=tier, result=result, service=service)._value.get()


class TestServiceLabel:
    """The label must stay bounded: the raw endpoint carries coordinates."""

    @pytest.mark.parametrize(
        "endpoint,expected",
        [
            ("/route/v1/driving/-84.09,9.92;-84.08,9.93", "route"),
            ("/table/v1/driving/-84.09,9.92", "table"),
            ("/match/v1/driving/-84.09,9.92", "match"),
            ("/trip/v1/driving/-84.09,9.92", "trip"),
            ("/nearest/v1/driving/-84.09,9.92", "nearest"),
        ],
    )
    def test_known_services(self, endpoint: str, expected: str):
        assert _service_label(endpoint) == expected

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/tile/v1/driving/tile(1,2,3).mvt",
            "/unknown/v1/driving/-84.09,9.92",
            "/test",
            "",
        ],
    )
    def test_unknown_collapses_to_other(self, endpoint: str):
        """Anything off the allowlist must not mint a new series."""
        assert _service_label(endpoint) == "other"

    def test_distinct_coordinates_share_one_label(self):
        a = _service_label("/route/v1/driving/-84.09,9.92;-84.08,9.93")
        b = _service_label("/route/v1/driving/-10.00,1.11;-20.00,2.22")
        assert a == b == "route"


@pytest.mark.asyncio
async def test_l1_miss_then_hit_is_counted():
    client = OSRMClient()
    response_cache.clear()
    endpoint = "/route/v1/driving/-84.09,9.92;-84.08,9.93"

    miss_before = _count("l1", "miss", "route")
    hit_before = _count("l1", "hit", "route")

    with patch.object(client, "_fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"code": "Ok"}
        await client._get(endpoint, {"a": "1"})   # populates L1
        await client._get(endpoint, {"a": "1"})   # served by L1

    assert _count("l1", "miss", "route") == miss_before + 1
    assert _count("l1", "hit", "route") == hit_before + 1
    await client.close()


@pytest.mark.asyncio
async def test_absent_l2_records_nothing():
    """An unconfigured tier is not a miss -- it must not be counted at all."""
    client = OSRMClient()
    response_cache.clear()
    endpoint = "/nearest/v1/driving/-84.09,9.92"

    before = (_count("l2", "hit", "nearest"), _count("l2", "miss", "nearest"))

    with patch.object(client, "_fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"code": "Ok"}
        await client._get(endpoint, {"a": "1"})

    assert (_count("l2", "hit", "nearest"), _count("l2", "miss", "nearest")) == before
    await client.close()


@pytest.mark.asyncio
async def test_l2_miss_then_hit_is_counted():
    client = OSRMClient()
    response_cache.clear()
    endpoint = "/table/v1/driving/-84.09,9.92"
    payload = {"code": "Ok"}

    miss_before = _count("l2", "miss", "table")
    hit_before = _count("l2", "hit", "table")

    redis_cache = AsyncMock()
    redis_cache.available = True
    redis_cache.get.return_value = None          # L2 miss on the first read
    client.redis_cache = redis_cache

    with patch.object(client, "_fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = payload
        await client._get(endpoint, {"a": "1"})
        assert _count("l2", "miss", "table") == miss_before + 1

        # Drop L1 so the next read falls through to L2, which now holds the value.
        response_cache.clear()
        redis_cache.get.return_value = payload
        result = await client._get(endpoint, {"a": "1"})

    assert result == payload
    assert _count("l2", "hit", "table") == hit_before + 1
    await client.close()


@pytest.mark.asyncio
async def test_l2_hit_promotes_into_l1():
    """A payload served by L2 must land in L1 so the next read stops there."""
    client = OSRMClient()
    response_cache.clear()
    endpoint = "/match/v1/driving/-84.09,9.92"
    payload = {"code": "Ok"}

    redis_cache = AsyncMock()
    redis_cache.available = True
    redis_cache.get.return_value = payload
    client.redis_cache = redis_cache

    hit_before = _count("l1", "hit", "match")

    with patch.object(client, "_fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        await client._get(endpoint, {"a": "1"})   # L1 miss -> L2 hit -> promote
        await client._get(endpoint, {"a": "1"})   # now an L1 hit
        assert mock_fetch.call_count == 0

    assert _count("l1", "hit", "match") == hit_before + 1
    assert redis_cache.get.call_count == 1
    await client.close()
