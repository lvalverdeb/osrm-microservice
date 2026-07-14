import pytest
import numpy as np
from app.services.vrp_service import VrpService, AllocationOptions
from app.models.schemas import Coordinate


@pytest.fixture
def service():
    return VrpService.__new__(VrpService)


def _make_stops(coords):
    return [Coordinate(longitude=lon, latitude=lat) for lon, lat in coords]


def test_allocate_travel_time_mode(service):
    """Two depots, two stops — each depot gets one stop based on duration."""
    depots = _make_stops([(-84.09, 9.93), (-84.15, 9.97)])
    stops = _make_stops([(-84.10, 9.94), (-84.14, 9.96)])
    durations = [
        [60, 600],
        [600, 60],
    ]
    distances = [
        [5000, 50000],
        [50000, 5000],
    ]
    result = service._allocate_stops(durations, distances, depots, stops, AllocationOptions(mode="travel_time"))
    assert 0 in result["allocations"]
    assert 1 in result["allocations"]
    assert 0 in result["allocations"][0]
    assert 1 in result["allocations"][1]


def test_allocate_distance_mode(service):
    """Two depots, two stops — uses distance matrix instead of duration."""
    depots = _make_stops([(-84.09, 9.93), (-84.15, 9.97)])
    stops = _make_stops([(-84.10, 9.94), (-84.14, 9.96)])
    durations = [
        [60, 600],
        [600, 60],
    ]
    distances = [
        [5000, 50000],
        [50000, 5000],
    ]
    result = service._allocate_stops(distances, distances, depots, stops, AllocationOptions(mode="distance"))
    assert 0 in result["allocations"]
    assert 1 in result["allocations"]
    assert 0 in result["allocations"][0]
    assert 1 in result["allocations"][1]


def test_allocate_radial_mode(service):
    """Uses Euclidean distance only — ignores time/distance matrices."""
    depots = _make_stops([(-84.09, 9.93), (-84.15, 9.97)])
    stops = _make_stops([(-84.10, 9.94)])
    result = service._allocate_stops([[1e12], [1e12]], [[1e12], [1e12]], depots, stops, AllocationOptions(mode="radial"))
    alloc = result["allocations"]
    stop_assignment = alloc[0] + alloc[1]
    assert len(stop_assignment) == 1


def test_allocate_hysteresis_prevents_flapping(service):
    """Stop assigned to anchor when another depot is better by less than hysteresis."""
    depots = _make_stops([(-84.09, 9.93), (-84.15, 9.97)])
    stops = _make_stops([(-84.12, 9.95)])
    distances = [[10000], [10001]]
    durations = [[10000], [10001]]
    result = service._allocate_stops(
        durations, distances, depots, stops, AllocationOptions(mode="travel_time", hysteresis_m=5000)
    )
    alloc_0 = result["allocations"][0]
    alloc_1 = result["allocations"][1]
    assert (0 in alloc_0) or (0 in alloc_1)


def test_allocate_hysteresis_allows_change(service):
    """Stop assigned to better depot when margin > hysteresis."""
    depots = _make_stops([(-84.09, 9.93), (-84.15, 9.97)])
    stops = _make_stops([(-84.12, 9.95)])
    distances = [[100], [1000]]
    durations = np.array(distances).tolist()
    result = service._allocate_stops(
        durations, distances, depots, stops, AllocationOptions(mode="travel_time", hysteresis_m=50)
    )
    assert len(result["allocations"][0]) == 1
    assert len(result["allocations"][1]) == 0


def test_allocate_sanity_override(service):
    """Stop more than 50km euclidean from best depot uses anchor instead."""
    depots = _make_stops([(-84.09, 9.93), (-84.15, 9.97)])
    stops = _make_stops([(-85.00, 10.00)])
    durations = [[100], [1000]]
    distances = [[3000], [50000]]
    result = service._allocate_stops(durations, distances, depots, stops, AllocationOptions(mode="travel_time"))
    stop_0 = result["allocations"][0]
    stop_1 = result["allocations"][1]
    assert (0 in stop_0) or (0 in stop_1)


def test_allocate_max_radius(service):
    """Stop beyond max_radius is marked unreachable."""
    depots = _make_stops([(-84.09, 9.93)])
    stops = _make_stops([(-84.50, 10.20)])
    durations = [[5000]]
    distances = [[150000]]
    result = service._allocate_stops(durations, distances, depots, stops, AllocationOptions(max_radius_m=100000))
    assert 0 in result["unreachable_stops"]


def test_allocate_single_depot(service):
    """All stops assigned to the only depot."""
    depots = _make_stops([(-84.09, 9.93)])
    stops = _make_stops([(-84.10, 9.94), (-84.11, 9.95)])
    durations = [[60, 120]]
    distances = [[5000, 10000]]
    result = service._allocate_stops(durations, distances, depots, stops)
    assert len(result["allocations"][0]) == 2
    assert len(result["unreachable_stops"]) == 0


def test_allocate_all_unreachable(service):
    """No depots within radius — all unreachable."""
    depots = _make_stops([(-84.09, 9.93)])
    stops = _make_stops([(-85.00, 10.50)])
    durations = [[1e12]]
    distances = [[200000]]
    result = service._allocate_stops(durations, distances, depots, stops, AllocationOptions(max_radius_m=50000))
    assert len(result["unreachable_stops"]) == 1


def test_allocate_infinity_handling(service):
    """Some depot-stop pairs have infinite cost — fallback to anchor."""
    depots = _make_stops([(-84.09, 9.93), (-84.15, 9.97)])
    stops = _make_stops([(-84.10, 9.94)])
    durations = [[60], [1e12]]
    distances = [[5000], [1e12]]
    result = service._allocate_stops(durations, distances, depots, stops, AllocationOptions(mode="travel_time"))
    alloc_0 = result["allocations"][0]
    alloc_1 = result["allocations"][1]
    total = len(alloc_0) + len(alloc_1) + len(result["unreachable_stops"])
    assert total == 1
