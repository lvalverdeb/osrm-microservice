"""The advertised matrix bound and the engine's actual bound are one number.

osrm-routed refuses a table request when `sources x destinations` exceeds
`--max-table-size` squared. These tests pin the gateway to that same rule, for
client requests and for the matrix batching VRP does on their behalf.
"""
import httpx
import pytest
from httpx import AsyncClient

from app import main
from app.config import settings
from app.models.schemas import Coordinate, MatrixRequest
from app.services.vrp_service import VrpService


def _coords(count: int) -> list[dict[str, float]]:
    return [{"longitude": -84.09, "latitude": 9.93} for _ in range(count)]


def _client() -> AsyncClient:
    return AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


@pytest.mark.asyncio
async def test_symmetric_matrix_beyond_the_budget_is_rejected():
    """A symmetric matrix costs n^2 cells, so the budget binds at its square root."""
    side = int(settings.MATRIX_MAX_CELLS**0.5)
    async with _client() as ac:
        response = await ac.post("/matrix", json={"coordinates": _coords(side + 1)})
    assert response.status_code == 422
    assert str(settings.MATRIX_MAX_CELLS) in response.text


def test_asymmetric_matrix_within_the_budget_is_accepted():
    """Few sources against many destinations is cheap, and must stay available.

    Far more coordinates than the symmetric limit allows, because only the
    product is charged -- this is the shape the VRP path depends on.
    """
    sources = 4
    destinations = settings.MATRIX_MAX_CELLS // sources
    request = MatrixRequest(
        coordinates=[Coordinate(longitude=-84.09, latitude=9.93)] * (destinations + sources),
        sources=list(range(sources)),
        destinations=list(range(sources, destinations + sources)),
    )
    assert len(request.sources) * len(request.destinations) == settings.MATRIX_MAX_CELLS
    assert len(request.coordinates) > int(settings.MATRIX_MAX_CELLS**0.5)


def test_vrp_batches_stops_within_the_cell_budget(monkeypatch):
    """VRP's own matrix calls must satisfy the limit it imposes on clients."""
    sent: list[MatrixRequest] = []

    async def capture(request: MatrixRequest) -> dict:
        sent.append(request)
        return {"durations": [[0.0] * len(request.destinations)] * len(request.sources),
                "distances": [[0.0] * len(request.destinations)] * len(request.sources)}

    service = VrpService(osrm_client=None)
    monkeypatch.setattr(service, "osrm_client", type("C", (), {"get_matrix": staticmethod(capture)})())

    depots = [Coordinate(longitude=-84.09, latitude=9.93)] * 500
    stops = [Coordinate(longitude=-84.10, latitude=9.94)] * 600

    import asyncio
    asyncio.run(service._get_depot_to_stop_matrix(depots, stops))

    assert sent, "expected at least one matrix request"
    for request in sent:
        cells = len(request.sources) * len(request.destinations)
        assert cells <= settings.MATRIX_MAX_CELLS
