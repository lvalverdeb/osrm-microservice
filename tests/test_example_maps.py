"""`examples/src/maps.py` -- the shared floor under the examples that draw.

Only the geometry is tested. Whether a polygon is the right shade of blue is
not checkable and not interesting; whether it is the right polygon is both.

`hull` earned this file. Its first version walked the points and their reverse
with one stack, which is the monotone-chain algorithm with its two halves run
together. It returned three corners of a square and collapsed three collinear
points to a single point -- and drew a plausible-looking territory either way,
which is exactly the failure a map cannot show you. The random cross-check
below is the test that would have caught it on the first run.
"""

from __future__ import annotations

import itertools
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "examples" / "src"))

import maps

SQUARE = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]


def on_hull(points: list[tuple[float, float]]) -> set[tuple[float, float]]:
    """Hull vertices, found the slow obvious way.

    A pair is a hull edge when every other point lies on one side of it. This
    is O(n^3) and shares no code with `maps.hull`, which is the whole point:
    an independent answer is the only kind worth checking against.
    """
    distinct = sorted(set(points))
    vertices: set[tuple[float, float]] = set()
    for a, b in itertools.permutations(distinct, 2):
        others = [c for c in distinct if c not in (a, b)]
        if all(maps._cross(a, b, c) >= 0 for c in others):
            vertices |= {a, b}
    return vertices


def test_the_hull_of_a_square_keeps_all_four_corners():
    """The exact shape the first implementation lost."""
    assert set(maps.hull(SQUARE)) == set(SQUARE)
    assert len(maps.hull(SQUARE)) == 4


def test_a_point_inside_is_not_a_corner():
    assert set(maps.hull([*SQUARE, (0.5, 0.5)])) == set(SQUARE)


def test_collinear_points_reduce_to_their_endpoints():
    """Three points on a line bound no area, so only the ends survive."""
    assert maps.hull([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]) == [(0.0, 0.0), (2.0, 2.0)]


def test_repeated_points_do_not_multiply_vertices():
    assert len(maps.hull([*SQUARE, *SQUARE, *SQUARE])) == 4


def test_the_hull_agrees_with_brute_force_on_random_clouds():
    """The property, over shapes nobody chose to be flattering."""
    rng = random.Random(7)
    for _ in range(200):
        cloud = [(round(rng.uniform(0, 10), 2), round(rng.uniform(0, 10), 2))
                 for _ in range(rng.randint(3, 12))]
        assert set(maps.hull(cloud)) == on_hull(cloud), cloud


def test_area_is_for_comparing_hulls_not_measuring_them():
    """The unit square is 1; half of it is 0.5; a line encloses nothing."""
    assert maps.area(maps.hull(SQUARE)) == pytest.approx(1.0)
    triangle = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    assert maps.area(maps.hull(triangle)) == pytest.approx(0.5)
    assert maps.area(maps.hull([(0.0, 0.0), (1.0, 1.0)])) == 0.0


def test_area_does_not_depend_on_where_the_ring_starts():
    ring = maps.hull(SQUARE)
    rotated = ring[2:] + ring[:2]
    assert maps.area(rotated) == pytest.approx(maps.area(ring))


def test_a_group_too_small_to_bound_an_area_reports_it():
    """`region` returns whether it drew, so a caller can say so out loud.

    A territory of two stops is a real answer -- it is what a sparse round
    produces -- and silently drawing nothing would read as a missing feature.
    """
    canvas = maps.base_map(SQUARE)
    assert maps.region(canvas, SQUARE, "#000000", "four") is True
    assert maps.region(canvas, SQUARE[:2], "#000000", "two") is False


def test_a_map_of_nothing_is_refused():
    """Defaulting to a centre would put every empty round in the same wrong place."""
    with pytest.raises(ValueError, match="at least one point"):
        maps.base_map([])


def test_the_palette_is_stable_across_calls():
    """Colour carries meaning here, so it has to survive a re-run.

    `fleet/visualize_vrp.py` seeds its vehicle colours from `random.randint`,
    so the same van is a different colour in two runs of the same script and
    two maps cannot be compared.
    """
    assert maps.colour(2) == maps.colour(2)
    assert maps.colour(0) != maps.colour(1)
    assert maps.colour(len(maps.COLOURS)) == maps.colour(0)
