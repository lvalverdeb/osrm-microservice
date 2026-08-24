"""Quality assertions for `/vrp` and `/vrp/allocate`.

These endpoints are not required to produce identical routes -- clustering ties
and float details legitimately differ -- so equality is the wrong question. What
must hold is split in two:

**Per-side invariants** are checked against each response independently. They
catch the class of bug a cross-comparison is structurally blind to: a port that
silently drops stops scores *better* on total distance, and two implementations
that are wrong in the same way agree with each other perfectly.

**Cross-side quality** compares the two. Per-case distance comparison is too
weak to gate on -- a port that is systematically worse passes on a lucky seed --
so the verdict is taken from the distribution across the whole corpus.
"""

from __future__ import annotations

import statistics
from typing import Any

from parity.compare import Diff, Verdict

# `_build_chunk_requests` caps a route at min(VRP_CHUNK_SIZE, capacity). The
# gateway does not expose VRP_CHUNK_SIZE, so the harness has to be told; this
# mirrors deploy/env/app.env.
DEFAULT_VRP_CHUNK_SIZE = 80

# A candidate route set may be fractionally worse from float ordering without
# meaning anything; beyond this it is a real regression.
MAX_ACCEPTABLE_RATIO = 1.10


def _fail(path: str, message: str) -> Diff:
    return Diff(path, message, None, None, Verdict.FAIL)


def vrp_invariants(request: dict[str, Any], response: dict[str, Any],
                   chunk_size: int = DEFAULT_VRP_CHUNK_SIZE) -> list[Diff]:
    """Check one `/vrp` response against the request that produced it.

    Args:
        request: The request body sent.
        response: The decoded response body.
        chunk_size: `VRP_CHUNK_SIZE`, which bounds a route jointly with capacity.

    Returns:
        Every violated invariant.
    """
    diffs: list[Diff] = []
    if response.get("code") != "Ok":
        diffs.append(_fail("$.code", f"expected Ok, got {response.get('code')!r}"))

    routes = response.get("routes", [])
    stop_count = len(request.get("stops", []))
    limit = min(chunk_size, request.get("capacity", chunk_size))

    diffs.extend(_route_invariants(routes, stop_count, len(request.get("depots", [])), limit))
    diffs.extend(_coverage_invariants(routes, stop_count))
    diffs.extend(_total_invariants(routes, response))
    return diffs


def _route_invariants(routes: list[dict], stop_count: int, depot_count: int,
                      limit: int) -> list[Diff]:
    """Check each route's capacity, depot reference and geometry."""
    diffs: list[Diff] = []
    for position, route in enumerate(routes):
        path = f"$.routes[{position}]"
        indices = route.get("stops_indices", [])
        if len(indices) > limit:
            diffs.append(_fail(f"{path}.stops_indices",
                               f"{len(indices)} stops exceeds min(chunk_size, capacity) = {limit}"))
        if not 0 <= route.get("depot_index", -1) < max(depot_count, 1):
            diffs.append(_fail(f"{path}.depot_index", f"out of range: {route.get('depot_index')}"))
        if any(not 0 <= index < stop_count for index in indices):
            diffs.append(_fail(f"{path}.stops_indices", "contains an out-of-range stop index"))
        diffs.extend(_geometry_invariants(f"{path}.route_geometry", route.get("route_geometry")))
    return diffs


def _geometry_invariants(path: str, geometry: Any) -> list[Diff]:
    """A route geometry must be a LineString a vehicle could actually drive."""
    if not isinstance(geometry, dict):
        return [_fail(path, "missing or not an object")]
    if geometry.get("type") != "LineString":
        return [_fail(path, f"expected LineString, got {geometry.get('type')!r}")]
    if len(geometry.get("coordinates", [])) < 2:
        return [_fail(path, "fewer than 2 coordinates")]
    return []


def _coverage_invariants(routes: list[dict], stop_count: int) -> list[Diff]:
    """No stop may be served twice.

    Completeness is deliberately not asserted: `/vrp` omits stops the allocation
    found unreachable, and its response carries no unreachable list to check
    against. Cross-side comparison covers that gap by requiring both sides to
    serve the same set.
    """
    served = [index for route in routes for index in route.get("stops_indices", [])]
    if len(served) != len(set(served)):
        duplicates = sorted({i for i in served if served.count(i) > 1})
        return [_fail("$.routes", f"stops served more than once: {duplicates}")]
    if len(served) > stop_count:
        return [_fail("$.routes", f"{len(served)} stops served but only {stop_count} requested")]
    return []


def _total_invariants(routes: list[dict], response: dict[str, Any]) -> list[Diff]:
    """Reported totals must equal the sum of the routes they summarise."""
    diffs: list[Diff] = []
    for field_name, key in (("total_distance", "distance_meters"),
                            ("total_duration", "duration_seconds")):
        expected = sum(route.get(key, 0.0) for route in routes)
        actual = response.get(field_name, 0.0)
        if not _close(expected, actual):
            diffs.append(_fail(f"$.{field_name}", f"{actual} != sum of routes ({expected})"))
    return diffs


def _close(a: float, b: float) -> bool:
    """Float-tolerant equality for summed totals."""
    return abs(a - b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))


def allocation_invariants(request: dict[str, Any], response: dict[str, Any]) -> list[Diff]:
    """Check that `/vrp/allocate` partitions the stops.

    Every stop must appear exactly once across the allocations and the
    unreachable list -- no duplicates, nothing lost.
    """
    diffs: list[Diff] = []
    if response.get("code") != "Ok":
        diffs.append(_fail("$.code", f"expected Ok, got {response.get('code')!r}"))

    assigned = [stop for stops in response.get("allocations", {}).values() for stop in stops]
    unreachable = response.get("unreachable_stops", [])
    placed = assigned + unreachable

    if len(placed) != len(set(map(str, placed))):
        diffs.append(_fail("$.allocations", "a stop appears more than once"))
    if len(placed) != len(request.get("stops", [])):
        diffs.append(_fail("$.allocations",
                           f"{len(placed)} stops placed, {len(request.get('stops', []))} requested"))
    return diffs


def served_stops(response: dict[str, Any]) -> set[int]:
    """Return the set of stop indices served by a `/vrp` response."""
    return {index for route in response.get("routes", [])
            for index in route.get("stops_indices", [])}


def allocation_agreement(reference: dict[str, Any], candidate: dict[str, Any]) -> float:
    """Return the fraction of stops both sides assigned to the same depot.

    Allocation is deterministic given the same cost matrix, so anything below
    1.0 means a genuine algorithmic difference -- most likely in exactly the
    place worth watching: `argmin` tie-breaking, or how the UNREACHABLE sentinel
    interacts with the hysteresis comparison.
    """
    ref_map = _stop_to_depot(reference)
    cand_map = _stop_to_depot(candidate)
    if not ref_map:
        return 1.0
    agreed = sum(1 for stop, depot in ref_map.items() if cand_map.get(stop) == depot)
    return agreed / len(ref_map)


def _stop_to_depot(response: dict[str, Any]) -> dict[str, str]:
    """Invert an allocation map into stop -> depot."""
    return {str(stop): str(depot)
            for depot, stops in response.get("allocations", {}).items()
            for stop in stops}


def ratio_verdict(ratios: list[float], epsilon: float = 1e-6) -> tuple[Verdict, str]:
    """Judge a candidate's solution quality from the distribution of ratios.

    Args:
        ratios: `candidate_total / reference_total`, one per case.
        epsilon: Slack on the median, absorbing float-ordering noise.

    Returns:
        The verdict and a one-line summary for the report.
    """
    if not ratios:
        return Verdict.OK, "no comparable cases"
    median = statistics.median(ratios)
    summary = (f"distance ratio med {median:.3f} "
               f"p95 {_percentile(ratios, 0.95):.3f} max {max(ratios):.3f}")
    if median > 1.0 + epsilon or max(ratios) > MAX_ACCEPTABLE_RATIO:
        return Verdict.FAIL, summary
    if any(ratio > 1.0 + epsilon for ratio in ratios):
        return Verdict.ADVISORY, summary
    return Verdict.OK, summary


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile, matching loadtest.run's definition."""
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]
