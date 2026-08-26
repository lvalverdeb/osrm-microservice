"""Tiling and pair-level caching — MTX-6, MTX-7, MTX-10, T-11, E-11.

Two things the gateway cannot do for us, and one it already does.

**Tiling (MTX-7).** n² is the scaling wall: 5,000 locations is 25M cells, and
the gateway refuses anything past `MATRIX_MAX_CELLS` (10,000) with a 422. So a
large matrix must be requested as tiles over `sources`/`destinations` and
reassembled. The acceptance condition is exact equality with the unchunked call
— a tiling that is merely close is a tiling that is wrong somewhere.

**Pair caching (MTX-10).** The gateway caches whole requests, keyed on the
endpoint path plus a digest of the params, and the coordinates live in that
path. Change one stop and the key changes, so every pair is refetched. MTX-10
wants ≥90% pair reuse on incremental days, which that structure cannot give at
any hit rate — the reuse has to be per pair, above the gateway.

**Versioning (MTX-6)** already landed with E-10's `matrix_version`.

The reassembly tests run against the synthetic engine so the numbers are real;
the tiling arithmetic is a pure function and is checked at 5,000-location scale
without calling anything.
"""

from __future__ import annotations

import typing

import pytest
from conftest_gateway import requires_binary
from conftest_synthetic import requires_engine

from vrp.matrix import PairCache, build_large_matrix, plan_tiles

# The mainland, as (latitude, longitude).
N1, N2, N3, N4 = (0.0, 0.0), (0.0, 0.01), (0.0, 0.02), (0.01, 0.02)


# --------------------------------------------------------------------------
# Tiling arithmetic — pure, so it can be checked at real scale
# --------------------------------------------------------------------------

def test_a_small_matrix_needs_no_tiling():
    """Below the cap, one tile. Splitting anyway would cost round trips for
    nothing and make the common case pay for the rare one."""
    tiles = plan_tiles(size=50, max_cells=10_000)
    assert len(tiles) == 1
    assert tiles[0].sources == range(50)
    assert tiles[0].destinations == range(50)


def test_every_tile_respects_the_cell_cap():
    """The cap is the gateway's, and it answers 422 rather than truncating."""
    for size in (120, 500, 5_000):
        for tile in plan_tiles(size=size, max_cells=10_000):
            cells = len(tile.sources) * len(tile.destinations)
            assert 0 < cells <= 10_000, f"{size}: tile of {cells} cells"


def test_tiles_cover_every_cell_exactly_once():
    """Gaps leave holes in the matrix; overlaps waste a round trip and invite
    two different answers for one pair."""
    size = 250
    seen: set[tuple[int, int]] = set()
    for tile in plan_tiles(size=size, max_cells=10_000):
        for i in tile.sources:
            for j in tile.destinations:
                assert (i, j) not in seen, f"cell ({i},{j}) covered twice"
                seen.add((i, j))
    assert len(seen) == size * size


def test_five_thousand_locations_is_tiled_rather_than_refused():
    """MTX-7's stated wall: 5,000 locations is 25M cells against a 10k cap.

    The count is what makes it a budget question rather than an impossibility,
    so it is asserted rather than left implicit.
    """
    tiles = plan_tiles(size=5_000, max_cells=10_000)
    assert sum(len(t.sources) * len(t.destinations) for t in tiles) == 5_000 ** 2
    assert len(tiles) == 2_500_000 // 1_000 or len(tiles) > 1_000


# --------------------------------------------------------------------------
# Reassembly against a live engine
# --------------------------------------------------------------------------

@pytest.mark.usefixtures("synthetic_gateway")
class TestReassembly:
    pytestmark: typing.ClassVar = [requires_engine, requires_binary]

    def test_tiled_build_equals_the_unchunked_call(self, synthetic_gateway):
        """E-11's acceptance condition, stated exactly.

        A cap of 4 cells forces a 2x2 grid of tiles over four locations, so
        every tile boundary is exercised. The result must be identical to the
        single call, cell for cell — not close.
        """
        from vrp.osrm import build_matrix

        whole, _ = build_matrix(synthetic_gateway, [N1, N2, N3, N4])
        tiled, _ = build_large_matrix(synthetic_gateway, [N1, N2, N3, N4],
                                      max_cells=4)

        assert tiled.durations == whole.durations
        assert tiled.distances == whole.distances
        assert tiled.version == whole.version, "same content, same version"

    def test_a_tiled_build_preserves_asymmetry(self, synthetic_gateway):
        """Tiling transposes indices, which is exactly where a d(i,j)/d(j,i)
        swap hides. The mainland is symmetric, so this uses the one-way island
        where the two directions genuinely differ."""
        from vrp.model import UNREACHABLE
        from vrp.osrm import build_matrix

        island = [(0.05, 0.05), (0.05, 0.06)]
        whole, _ = build_matrix(synthetic_gateway, island)
        tiled, _ = build_large_matrix(synthetic_gateway, island, max_cells=1)

        assert tiled.durations == whole.durations
        assert tiled.durations[1][0] == UNREACHABLE
        assert tiled.durations[0][1] != UNREACHABLE


# --------------------------------------------------------------------------
# Pair-level cache (MTX-10)
# --------------------------------------------------------------------------

def test_the_cache_reports_its_hit_rate():
    """NFR-06 asks for the hit rate to be reported. A cache whose hit rate is
    unknown cannot be shown to meet MTX-10's 90%, so this is the measurement,
    not a nicety."""
    cache = PairCache()
    assert cache.hit_rate == 0.0        # no lookups yet, not a division error

    cache.put((0.0, 0.0), (1.0, 1.0), "driving", duration=60, distance=1000)
    assert cache.get((0.0, 0.0), (1.0, 1.0), "driving") == (60, 1000)
    assert cache.get((0.0, 0.0), (2.0, 2.0), "driving") is None

    assert cache.hits == 1 and cache.misses == 1
    assert cache.hit_rate == pytest.approx(0.5)


def test_the_cache_distinguishes_profiles():
    """MTX-1: matrices are per profile. A van and a bicycle travelling between
    the same two points do not take the same time, and a cache that conflated
    them would be worse than no cache."""
    cache = PairCache()
    cache.put((0.0, 0.0), (1.0, 1.0), "driving", duration=60, distance=1000)

    assert cache.get((0.0, 0.0), (1.0, 1.0), "driving") is not None
    assert cache.get((0.0, 0.0), (1.0, 1.0), "cycling") is None


def test_the_cache_keeps_direction():
    """d(i,j) is not d(j,i) (MTX-2), so the key is ordered."""
    cache = PairCache()
    cache.put((0.0, 0.0), (1.0, 1.0), "driving", duration=60, distance=1000)

    assert cache.get((1.0, 1.0), (0.0, 0.0), "driving") is None


def test_pair_reuse_falls_off_quadratically_with_stop_churn():
    """The relationship behind MTX-10's 90%, which is not linear.

    A matrix is pairs, not stops, so replacing k of n stops keeps only
    ((n-k)/n)^2 of the pairs. Swapping 2 stops in 20 -- 10% churn -- leaves 81%
    of pairs, not 90%. This test asserts the law rather than one number,
    because the law is what tells an operator whether their day qualifies as
    "stable operations".
    """
    def reuse(total: int, replaced: int) -> float:
        yesterday = [(9.9 + i / 1000, -84.0) for i in range(total)]
        today = yesterday[:total - replaced] + [
            (9.95 + i / 1000, -84.1) for i in range(replaced)]
        cache = PairCache()
        for origin in yesterday:
            for destination in yesterday:
                cache.put(origin, destination, "driving", duration=1, distance=1)
        cache.reset_stats()
        for origin in today:
            for destination in today:
                cache.get(origin, destination, "driving")
        return cache.hit_rate

    for total, replaced in ((20, 2), (50, 5), (100, 3)):
        expected = ((total - replaced) / total) ** 2
        assert reuse(total, replaced) == pytest.approx(expected, abs=0.01)


def test_a_stable_day_meets_the_ninety_percent_target():
    """MTX-10: "incremental days reuse >= 90% of pairs in stable operations".

    Quadratic fall-off puts a real bound on what "stable" can mean: 90% pair
    reuse needs (1 - k/n)^2 >= 0.9, so k/n <= 5.1%. One stop in twenty is
    inside it; two is not, at 81%.
    """
    yesterday = [(9.9 + i / 1000, -84.0) for i in range(20)]
    today = yesterday[:19] + [(9.95, -84.1)]        # one stop replaced, 5%

    cache = PairCache()
    for origin in yesterday:
        for destination in yesterday:
            cache.put(origin, destination, "driving", duration=1, distance=1)

    cache.reset_stats()
    for origin in today:
        for destination in today:
            cache.get(origin, destination, "driving")

    assert cache.hit_rate >= 0.90, (
        f"only {cache.hit_rate:.0%} of pairs reused at 5% stop churn")


def test_eviction_is_least_recently_used():
    """A bounded cache must drop the coldest pair, not the oldest insert:
    depot rows are touched every day and would be evicted by FIFO."""
    cache = PairCache(max_entries=2)
    cache.put((0.0, 0.0), (1.0, 1.0), "driving", duration=1, distance=1)
    cache.put((0.0, 0.0), (2.0, 2.0), "driving", duration=2, distance=2)

    cache.get((0.0, 0.0), (1.0, 1.0), "driving")          # touch the first
    cache.put((0.0, 0.0), (3.0, 3.0), "driving", duration=3, distance=3)

    assert cache.get((0.0, 0.0), (1.0, 1.0), "driving") is not None, "was touched"
    assert cache.get((0.0, 0.0), (2.0, 2.0), "driving") is None, "coldest, evicted"
