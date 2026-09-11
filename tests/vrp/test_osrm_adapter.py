"""Building a pinned TravelMatrix from a live engine — MTX-1…5, T-10, E-10.

Run against the synthetic map in `tests/synthetic/grid.osm`, whose geometry is
known by hand: three collinear nodes running east, one running north, and an
island joined to nothing. The island is the point of most of this file. MTX-5
requires unreachable pairs to survive as an explicit sentinel rather than a
large finite number, and the reason is stated in the spec: *large-finite
sentinels get "optimised into" solutions*. A 10⁹-metre arc is expensive but
finite, so a solver with nothing better will use it and hand back a plan
containing a leg no vehicle can drive.

This repository had that defect. Three examples wrote `10 ** 9` for a null cell
before E-10, which is exactly the failure MTX-5 describes.

The assertions are consequences of the map's layout, so a transposed
coordinate, a swapped source/destination, or a silently symmetric matrix breaks
them — none of which a recorded fixture would catch, since fixtures key on the
outgoing URL and replay whatever was recorded.
"""

from __future__ import annotations

import pytest
from conftest_gateway import requires_binary
from conftest_synthetic import requires_engine

from vrp.model import UNREACHABLE
from vrp.osrm import SnapWarning, build_matrix, matrix_version

pytestmark = [requires_engine, requires_binary]

# The mainland, as (latitude, longitude).
N1, N2, N3, N4 = (0.0, 0.0), (0.0, 0.01), (0.0, 0.02), (0.01, 0.02)
# The island, reachable from nowhere on the mainland.
N5, N6 = (0.05, 0.05), (0.05, 0.06)

LEG_METRES = 1113.0
TOLERANCE = 60.0


def test_both_duration_and_distance_are_retrieved(synthetic_gateway):
    """MTX-3: costing needs both, so an adapter fetching one is incomplete."""
    matrix, _ = build_matrix(synthetic_gateway, [N1, N2, N3], profile="driving")

    assert matrix.duration(0, 1) > 0
    assert matrix.distance(0, 1) == pytest.approx(LEG_METRES, abs=TOLERANCE)
    assert matrix.distance(0, 2) == pytest.approx(2 * LEG_METRES, abs=TOLERANCE)


def test_the_matrix_is_square_and_zero_on_the_diagonal(synthetic_gateway):
    matrix, _ = build_matrix(synthetic_gateway, [N1, N2, N3, N4], profile="driving")

    assert len(matrix.durations) == 4
    assert all(len(row) == 4 for row in matrix.durations)
    assert all(matrix.duration(i, i) == 0 for i in range(4))
    assert all(matrix.distance(i, i) == 0 for i in range(4))


def test_unreachable_pairs_carry_the_sentinel_not_a_large_number(synthetic_gateway):
    """MTX-5, and the reason E-10 exists.

    The island is joined to the mainland by nothing, so no route exists in
    either direction. Those cells must be the sentinel — not zero, which reads
    as "already there", and not 10⁹, which reads as "far but possible".
    """
    matrix, _ = build_matrix(synthetic_gateway, [N1, N2, N5, N6], profile="driving")

    assert matrix.durations[0][2] == UNREACHABLE
    assert matrix.durations[2][0] == UNREACHABLE
    assert matrix.distances[0][2] == UNREACHABLE
    # ...while the pairs within each component stay ordinary numbers.
    assert matrix.durations[0][1] > 0
    assert matrix.durations[2][3] > 0


def test_reading_an_unreachable_arc_raises_rather_than_returning_a_number():
    """A sentinel that arithmetic can consume is a large finite number wearing
    a different hat. `duration()` refuses; callers ask `is_reachable` first."""
    from vrp.model import TravelMatrix, UnreachableArc

    matrix = TravelMatrix(
        version="t", durations=((0, UNREACHABLE), (5, 0)),
        distances=((0, UNREACHABLE), (9, 0)))

    assert not matrix.is_reachable(0, 1)
    assert matrix.is_reachable(1, 0)
    with pytest.raises(UnreachableArc):
        matrix.duration(0, 1)
    with pytest.raises(UnreachableArc):
        matrix.distance(0, 1)
    assert matrix.duration(1, 0) == 5


def test_snap_distances_are_recorded(synthetic_gateway):
    """MTX-4: every location snapped, with the distance recorded.

    A point on the road snaps at roughly zero; one off it snaps further. Both
    are legitimate — what MTX-4 forbids is not knowing which happened.
    """
    on_road = (0.0, 0.005)              # midway along East Road
    _, snaps = build_matrix(synthetic_gateway, [N1, on_road], profile="driving")

    assert len(snaps) == 2
    assert all(snap.distance_m >= 0 for snap in snaps)
    assert snaps[0].distance_m < 50, "n1 is a node on the network"
    assert all(snap.location == given
               for snap, given in zip(snaps, [N1, on_road], strict=True))


def test_a_far_snap_warns_rather_than_succeeding_silently(synthetic_gateway):
    """MTX-4: "snaps beyond a threshold raise a data-quality warning, not a
    silent success". A stop 2 km from any road is a data problem, and a plan
    built on it is servicing somewhere nobody asked for."""
    stranded = (0.02, 0.0)              # ~2.2 km north of East Road

    with pytest.warns(SnapWarning, match="snapped"):
        _, snaps = build_matrix(synthetic_gateway, [N1, stranded],
                                profile="driving", snap_threshold_m=500)
    assert snaps[1].distance_m > 500


def test_the_matrix_version_pins_the_profile(synthetic_gateway):
    """MTX-1 and MTX-6: matrices are per profile, and a plan pins its matrix.

    Two profiles over the same locations must not share a version, or INV-4
    would accept a plan checked against the wrong travel data.

    The second version is computed rather than fetched, and the change is the
    point. This used to call the gateway twice, once per profile, and pass --
    because the gateway answered both from the one car graph it had. It was
    asserting that two version strings differ while the travel data behind them
    was identical, which is the very thing MTX-1 forbids, in miniature. A
    gateway that serves no cycling graph now refuses the request, so the live
    half is driving and the profile-pinning half is checked against the pure
    function that does the pinning.
    """
    car, _ = build_matrix(synthetic_gateway, [N1, N2], profile="driving")
    bike_version = matrix_version([N1, N2], profile="cycling")

    assert car.version != bike_version
    assert "driving" in car.version
    assert "cycling" in bike_version


def test_the_version_is_content_addressed(synthetic_gateway):
    """MTX-6: same locations and profile, same version; move a point, and not.

    Content-addressing is what lets INV-4 detect a plan checked against a
    matrix that has since changed underneath it.
    """
    first, _ = build_matrix(synthetic_gateway, [N1, N2], profile="driving")
    again, _ = build_matrix(synthetic_gateway, [N1, N2], profile="driving")
    moved, _ = build_matrix(synthetic_gateway, [N1, N3], profile="driving")

    assert first.version == again.version
    assert first.version != moved.version


def test_the_matrix_is_asymmetric(synthetic_gateway):
    """MTX-2: one-way systems and turn restrictions make d(i,j) != d(j,i).

    Island Road is one-way eastbound, so n5 reaches n6 and n6 does not reach
    n5. An adapter that symmetrised the matrix — by averaging, or by filling
    the lower triangle from the upper — would report the two as equal.
    """
    matrix, _ = build_matrix(synthetic_gateway, [N5, N6], profile="driving")

    assert matrix.is_reachable(0, 1), "eastbound along the one-way"
    assert not matrix.is_reachable(1, 0), "westbound against it"
    assert matrix.durations[0][1] != matrix.durations[1][0]


# --------------------------------------------------------------------------
# Snapping is batched — MTX-4, and the gateway's own /nearest/batch
# --------------------------------------------------------------------------
# `_snap_all` sent one `/nearest` per location, so a 120-stop round opened 121
# connections before a single matrix cell was fetched. Measured against the
# deployed gateway, forty coordinates took forty requests and 0.56 s one at a
# time against one request and 0.01 s batched. That cost nothing while the
# examples computed straight-line matrices and never called out; once every
# example required a road matrix, running them in sequence saturated the
# gateway and eighteen failed on connection timeouts.


def count_posts(monkeypatch):
    """Count posts to the snapping endpoints, still calling the real gateway."""
    import httpx

    from vrp import osrm

    calls: list[str] = []
    real = httpx.post

    def counted(url, *args, **kwargs):
        if "/nearest" in url:
            calls.append(url)
        return real(url, *args, **kwargs)

    monkeypatch.setattr(osrm.httpx, "post", counted)
    return calls


def test_snapping_asks_once_rather_than_once_per_location(synthetic_gateway,
                                                          monkeypatch):
    calls = count_posts(monkeypatch)
    points = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002), (0.001, 0.0)]
    build_matrix(synthetic_gateway, points)
    assert len(calls) == 1, f"{len(points)} locations took {len(calls)} requests"
    assert calls[0].endswith("/nearest/batch")


def test_every_location_still_gets_its_own_snap(synthetic_gateway):
    """Batching must not lose the one-snap-per-location correspondence."""
    points = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)]
    _matrix, snaps = build_matrix(synthetic_gateway, points)
    assert len(snaps) == len(points)
    assert [snap.location for snap in snaps] == points


def test_a_far_snap_still_warns_when_batched(synthetic_gateway):
    """MTX-4 survives the change: the warning is about the answer, not the call."""
    with pytest.warns(SnapWarning):
        build_matrix(synthetic_gateway, [(0.0, 0.0), (0.02, 0.02)],
                     snap_threshold_m=1.0)


def test_a_short_answer_is_refused_rather_than_misaligned(monkeypatch):
    """Fewer results than coordinates must fail, not shift every snap by one.

    The failure batching invites: zip stops at the shorter sequence, so a
    gateway returning three snaps for four locations would silently hand the
    fourth location's plan the third one's road.
    """
    from vrp import osrm

    class Truncated:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [{"waypoints": [{"location": [0.0, 0.0],
                                                "distance": 1.0,
                                                "name": "only one"}]}]}

    monkeypatch.setattr(osrm.httpx, "post", lambda *a, **k: Truncated())
    with pytest.raises(ValueError):
        osrm._snap_all("http://gateway", [(0.0, 0.0), (0.1, 0.1)], "driving",
                       50.0, 5.0)
