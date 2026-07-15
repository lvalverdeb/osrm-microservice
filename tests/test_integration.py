import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.integration
@pytest.mark.asyncio
async def test_route_endpoint_integration():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/route", json={
            "origin": {"longitude": -84.0907, "latitude": 9.9281},
            "destination": {"longitude": -84.0833, "latitude": 9.9333}
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "routes" in data
    assert len(data["routes"]) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_matrix_endpoint_integration():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/matrix", json={
            "coordinates": [
                {"longitude": -84.0907, "latitude": 9.9281},
                {"longitude": -84.0833, "latitude": 9.9333}
            ]
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "durations" in data or "distances" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint_integration():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "osrm_backend" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vrp_basic_integration():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/vrp", json={
            "depots": [{"longitude": -84.09, "latitude": 9.93}],
            "stops": [
                {"longitude": -84.10, "latitude": 9.94},
                {"longitude": -84.08, "latitude": 9.95},
                {"longitude": -84.11, "latitude": 9.92}
            ]
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "routes" in data
    assert len(data["routes"]) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vrp_unreachable_stops():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/vrp/allocate", json={
            "depots": [{"longitude": -84.09, "latitude": 9.93}],
            "stops": [
                {"longitude": -84.10, "latitude": 9.94},
                {"longitude": -50.00, "latitude": 0.00}
            ],
            "max_radius_km": 200
        })
    data = resp.json()
    assert "unreachable_stops" in data
