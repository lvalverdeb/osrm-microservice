"""TSP chunks within one solve run concurrently, but not without a bound.

A large solve used to be ~25 sequential /trip round trips. The fan-out has to
stay ordered, stay bounded, and not leave siblings running when one chunk fails.
"""
import asyncio

import pytest

from app.config import settings
from app.models.schemas import Stop, VehicleRoute, VrpRequest
from app.services.vrp_service import VrpService, _build_chunk_requests


def _request(num_stops: int, capacity: int = 10) -> VrpRequest:
    return VrpRequest(
        depots=[Stop(id="D1", longitude=-84.09, latitude=9.93)],
        stops=[Stop(id=f"S{i}", longitude=-84.10, latitude=9.94) for i in range(num_stops)],
        capacity=capacity,
    )


def _route(vehicle_id) -> VehicleRoute:
    return VehicleRoute(
        vehicle_id=vehicle_id,
        depot_index=0,
        stops_indices=[],
        route_geometry={"type": "LineString", "coordinates": []},
        distance_meters=1.0,
        duration_seconds=1.0,
    )


@pytest.mark.asyncio
async def test_chunks_run_concurrently_within_the_bound(monkeypatch):
    """Chunks overlap, but never more than VRP_CHUNK_CONCURRENCY at once."""
    service = VrpService(osrm_client=None)
    in_flight = 0
    peak = 0

    async def slow_chunk(chunk_request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return _route(chunk_request.vehicle_id)

    monkeypatch.setattr(service, "_solve_tsp_chunk", slow_chunk)

    # 10 stops at capacity 1 is 10 chunks -- more than the bound, so the bound
    # is what the peak should report rather than the chunk count.
    routes = await service._solve_depot_routes(_request(10, capacity=1), 0, list(range(10)), 0)

    assert len(routes) == 10
    assert peak > 1, "chunks did not overlap; the fan-out is still sequential"
    assert peak <= settings.VRP_CHUNK_CONCURRENCY


@pytest.mark.asyncio
async def test_routes_keep_chunk_order_regardless_of_completion_order(monkeypatch):
    """Concurrency must not reorder results: chunk N stays at position N."""
    service = VrpService(osrm_client=None)

    async def reversed_latency(chunk_request):
        # Later chunks finish first, so any order-by-completion shows up here.
        await asyncio.sleep(0.01 * (10 - chunk_request.original_indices[0]))
        return _route(chunk_request.vehicle_id)

    monkeypatch.setattr(service, "_solve_tsp_chunk", reversed_latency)

    routes = await service._solve_depot_routes(_request(5, capacity=1), 0, list(range(5)), 0)
    assert [r.vehicle_id for r in routes] == [f"D1-{i + 1}" for i in range(5)]


@pytest.mark.asyncio
async def test_one_failing_chunk_cancels_its_siblings(monkeypatch):
    """A failed solve must not leave chunks running against OSRM."""
    service = VrpService(osrm_client=None)
    completed = 0

    async def failing_chunk(chunk_request):
        nonlocal completed
        if chunk_request.original_indices[0] == 0:
            raise RuntimeError("chunk failed")
        await asyncio.sleep(0.05)
        completed += 1
        return _route(chunk_request.vehicle_id)

    monkeypatch.setattr(service, "_solve_tsp_chunk", failing_chunk)

    with pytest.raises(BaseExceptionGroup):
        await service._solve_depot_routes(_request(8, capacity=1), 0, list(range(8)), 0)

    await asyncio.sleep(0.1)
    assert completed == 0, "siblings kept running after the first failure"


def test_vehicle_numbering_without_depot_ids_accounts_for_earlier_depots():
    """The offset-based label must come from chunk position, not a filled list."""
    request = VrpRequest(
        depots=[Stop(longitude=-84.09, latitude=9.93)],  # id defaults to None
        stops=[Stop(longitude=-84.10, latitude=9.94) for _ in range(3)],
        capacity=1,
    )
    chunks = _build_chunk_requests(request, 0, [0, 1, 2], vehicle_offset=7)
    assert [c.vehicle_id for c in chunks] == [7, 8, 9]
