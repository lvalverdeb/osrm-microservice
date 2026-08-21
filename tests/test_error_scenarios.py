from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.osrm_client import OSRMClient


@pytest.mark.asyncio
async def test_missing_origin_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/route", json={"destination": {"longitude": 0, "latitude": 0}})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_coordinates_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/matrix", json={"coordinates": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_depots_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/vrp", json={"stops": [{"longitude": 0, "latitude": 0}]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_breadcrumbs_too_short_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/match", json={
            "breadcrumbs": [{"longitude": 0, "latitude": 0, "timestamp": 1000}]
        })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_profile_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/route", json={
            "origin": {"longitude": -84.09, "latitude": 9.93},
            "destination": {"longitude": -84.08, "latitude": 9.94},
            "profile": "flying"
        })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_osrm_down_propagation():
    client = OSRMClient()
    with patch.object(client, "_fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = httpx.ConnectError("Connection refused")
        with patch("app.main.osrm_client", client):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/route", json={
                    "origin": {"longitude": -84.09, "latitude": 9.93},
                    "destination": {"longitude": -84.08, "latitude": 9.94},
                })
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_tile_viewport_rejects_wrong_profile():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/tile/invalid/12/1/1.mvt")
    assert resp.status_code in (422, 500)


@pytest.mark.asyncio
async def test_invalid_coordinate_bounds_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/route", json={
            "origin": {"longitude": 200, "latitude": 9.93},
            "destination": {"longitude": -84.08, "latitude": 9.94},
        })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ready_returns_503_when_osrm_down():
    with patch("app.main.osrm_client.ping", new_callable=AsyncMock) as mock_ping:
        mock_ping.return_value = False
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["osrm_backend"] == "down"


@pytest.mark.asyncio
async def test_ready_returns_200_when_osrm_up():
    with patch("app.main.osrm_client.ping", new_callable=AsyncMock) as mock_ping:
        mock_ping.return_value = True
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_health_stays_200_while_degraded():
    """`/health` is for humans: it reports the degradation without failing."""
    with patch("app.main.osrm_client.ping", new_callable=AsyncMock) as mock_ping:
        mock_ping.return_value = False
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
