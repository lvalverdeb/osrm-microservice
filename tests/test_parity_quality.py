"""VRP quality assertions, each checked against a deliberately broken input.

An invariant that never fires is indistinguishable from one that is wrong, so
every check here is exercised in both directions.
"""

from __future__ import annotations

from parity.compare import Verdict, worst
from parity.quality import (
    allocation_agreement,
    allocation_invariants,
    ratio_verdict,
    vrp_invariants,
)

LINE = {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]}


def request(stops: int = 4, capacity: int = 35, depots: int = 1) -> dict:
    return {
        "depots": [{"longitude": 0.0, "latitude": 0.0, "id": f"d{i}"} for i in range(depots)],
        "stops": [{"longitude": 0.1 * i, "latitude": 0.1 * i, "id": f"s{i}"} for i in range(stops)],
        "capacity": capacity,
    }


def route(indices: list[int], distance: float = 10.0, duration: float = 20.0, depot: int = 0) -> dict:
    return {
        "vehicle_id": "d0",
        "depot_index": depot,
        "stops_indices": indices,
        "route_geometry": LINE,
        "distance_meters": distance,
        "duration_seconds": duration,
    }


def response(routes: list[dict]) -> dict:
    return {
        "code": "Ok",
        "routes": routes,
        "total_distance": sum(r["distance_meters"] for r in routes),
        "total_duration": sum(r["duration_seconds"] for r in routes),
    }


def test_valid_response_has_no_violations():
    assert vrp_invariants(request(), response([route([0, 1]), route([2, 3])])) == []


def test_duplicate_stop_is_caught():
    """A stop served twice is a bug that flatters the total-distance metric."""
    diffs = vrp_invariants(request(), response([route([0, 1]), route([1, 2])]))
    assert worst(diffs) is Verdict.FAIL
    assert "more than once" in diffs[0].message


def test_capacity_overflow_is_caught():
    """The bound is min(chunk_size, capacity), not capacity alone."""
    diffs = vrp_invariants(request(stops=6, capacity=2), response([route([0, 1, 2])]))
    assert worst(diffs) is Verdict.FAIL
    assert "exceeds min(chunk_size, capacity)" in diffs[0].message


def test_chunk_size_bounds_a_route_even_when_capacity_is_larger():
    diffs = vrp_invariants(request(stops=200, capacity=150),
                           response([route(list(range(100)))]), chunk_size=80)
    assert worst(diffs) is Verdict.FAIL


def test_out_of_range_stop_index_is_caught():
    diffs = vrp_invariants(request(stops=2), response([route([0, 5])]))
    assert worst(diffs) is Verdict.FAIL


def test_out_of_range_depot_index_is_caught():
    diffs = vrp_invariants(request(depots=1), response([route([0], depot=3)]))
    assert worst(diffs) is Verdict.FAIL


def test_totals_must_match_the_routes():
    broken = response([route([0]), route([1])])
    broken["total_distance"] = 999.0
    diffs = vrp_invariants(request(), broken)
    assert worst(diffs) is Verdict.FAIL
    assert "sum of routes" in diffs[0].message


def test_degenerate_geometry_is_caught():
    stub = route([0])
    stub["route_geometry"] = {"type": "LineString", "coordinates": [[0.0, 0.0]]}
    diffs = vrp_invariants(request(), response([stub]))
    assert worst(diffs) is Verdict.FAIL


def test_non_ok_code_is_caught():
    body = response([route([0])])
    body["code"] = "NoRoute"
    assert worst(vrp_invariants(request(), body)) is Verdict.FAIL


def test_serving_fewer_stops_than_requested_is_allowed():
    """Unreachable stops are legitimately omitted; the cross-side check catches
    a port that drops them for the wrong reason."""
    assert vrp_invariants(request(stops=4), response([route([0, 1])])) == []


def test_allocation_partition_is_valid():
    body = {"code": "Ok", "allocations": {"d0": ["s0", "s1"], "d1": ["s2"]},
            "unreachable_stops": ["s3"]}
    assert allocation_invariants(request(stops=4), body) == []


def test_allocation_duplicate_is_caught():
    body = {"code": "Ok", "allocations": {"d0": ["s0"], "d1": ["s0"]},
            "unreachable_stops": []}
    assert worst(allocation_invariants(request(stops=2), body)) is Verdict.FAIL


def test_allocation_losing_a_stop_is_caught():
    body = {"code": "Ok", "allocations": {"d0": ["s0"]}, "unreachable_stops": []}
    assert worst(allocation_invariants(request(stops=4), body)) is Verdict.FAIL


def test_agreement_is_one_when_allocations_match():
    body = {"allocations": {"d0": ["s0", "s1"]}}
    assert allocation_agreement(body, body) == 1.0


def test_agreement_falls_when_a_stop_moves_depot():
    ref = {"allocations": {"d0": ["s0", "s1"], "d1": []}}
    cand = {"allocations": {"d0": ["s0"], "d1": ["s1"]}}
    assert allocation_agreement(ref, cand) == 0.5


def test_ratio_verdict_passes_on_equal_or_better():
    verdict, _ = ratio_verdict([1.0, 0.98, 0.99])
    assert verdict is Verdict.OK


def test_ratio_verdict_flags_systematic_regression():
    """A port that is consistently worse must fail even when no single case is."""
    verdict, summary = ratio_verdict([1.02, 1.03, 1.01])
    assert verdict is Verdict.FAIL
    assert "med 1.020" in summary


def test_ratio_verdict_is_advisory_on_a_single_worse_case():
    verdict, _ = ratio_verdict([1.0, 0.97, 1.01, 0.98, 0.99])
    assert verdict is Verdict.ADVISORY


def test_ratio_verdict_fails_on_an_extreme_outlier():
    verdict, _ = ratio_verdict([0.9, 0.9, 0.9, 2.0])
    assert verdict is Verdict.FAIL
