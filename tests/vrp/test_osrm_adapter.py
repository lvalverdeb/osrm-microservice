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
from vrp.osrm import SnapWarning, build_matrix

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
    """
    car, _ = build_matrix(synthetic_gateway, [N1, N2], profile="driving")
    bike, _ = build_matrix(synthetic_gateway, [N1, N2], profile="cycling")

    assert car.version != bike.version
    assert "driving" in car.version


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
