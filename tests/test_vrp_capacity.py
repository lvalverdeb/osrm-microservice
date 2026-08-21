"""Capacity guards on the optimization endpoints.

Peak memory for a VRP solve is stops x concurrent solves. These tests cover
both bounds: the schema cap on a single request and the per-worker semaphore
that keeps a burst from multiplying peak RSS.
"""
import asyncio

import httpx
import pytest
from httpx import AsyncClient

from app import main
from app.config import settings

PAYLOAD = {
    "depots": [{"longitude": -84.09, "latitude": 9.93}],
    "stops": [{"longitude": -84.10, "latitude": 9.94}],
}


def _client() -> AsyncClient:
    return AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


@pytest.mark.asyncio
async def test_vrp_rejects_more_stops_than_the_cap():
    """Oversized payloads are refused by validation, not by the kernel."""
    payload = dict(PAYLOAD, stops=[
        {"longitude": -84.10, "latitude": 9.94} for _ in range(settings.VRP_MAX_STOPS + 1)
    ])
    async with _client() as ac:
        response = await ac.post("/vrp", json=payload)
    assert response.status_code == 422


def test_openapi_advertises_the_enforced_cap():
    """The documented maximum and the enforced maximum are the same number."""
    stops = main.app.openapi()["components"]["schemas"]["VrpRequest"]["properties"]["stops"]
    assert stops["maxItems"] == settings.VRP_MAX_STOPS


@pytest.mark.asyncio
async def test_vrp_sheds_load_when_every_slot_is_busy(monkeypatch):
    """A request that cannot get a slot in time gets 503, not an OOM kill."""
    monkeypatch.setattr(settings, "VRP_QUEUE_TIMEOUT", 0.05)
    occupied = asyncio.Event()
    release = asyncio.Event()

    async def blocking_solve(payload):
        occupied.set()
        await release.wait()
        raise RuntimeError("solve failed")

    monkeypatch.setattr(main.vrp_service, "solve_vrp", blocking_solve)

    async with _client() as ac:
        holder = asyncio.create_task(ac.post("/vrp", json=PAYLOAD))
        await asyncio.wait_for(occupied.wait(), timeout=5)

        shed = await ac.post("/vrp", json=PAYLOAD)
        assert shed.status_code == 503
        assert shed.headers["Retry-After"] == "1"

        release.set()
        assert (await holder).status_code == 500


@pytest.mark.asyncio
async def test_slot_is_released_after_a_failed_solve(monkeypatch):
    """A failing solve must not leak its slot, or the worker stops serving VRP."""
    async def failing_solve(payload):
        raise RuntimeError("solve failed")

    monkeypatch.setattr(main.vrp_service, "solve_vrp", failing_solve)

    async with _client() as ac:
        assert (await ac.post("/vrp", json=PAYLOAD)).status_code == 500
        assert (await ac.post("/vrp", json=PAYLOAD)).status_code == 500
