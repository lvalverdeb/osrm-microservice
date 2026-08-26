"""Local-search acceleration — ALG-2, T-33, E-33.

ALG-2 lists three accelerations and says of one of them: "This is the single
largest determinant of local-search throughput and MUST be implemented before
any tuning work." T-33's acceptance is a number — "≥ 10× local-search
throughput vs naive" — and E-33's row adds the important qualifier: *measured,
not asserted*.

So this file measures. The naive baseline is not a straw man: it is what a
competent person writes first, and each of its three properties is one the
accelerations remove.

    naive          every pair considered; route cost recomputed by summing the
                   whole route after each candidate move; every node revisited
                   on every sweep
    accelerated    candidates limited to the k nearest eligible neighbours;
                   move delta computed from the four affected edges alone;
                   nodes with a don't-look bit set skipped until something
                   incident to them changes

The correctness tests come first and matter more than the speed. A local search
that is fast and wrong is worse than the naive one, so both implementations
must reach the same *kind* of answer: a valid permutation, never worse than
where they started, and — on a small instance where it can be checked
exhaustively — a genuine local optimum.
"""

from __future__ import annotations

import time

import pytest

from vrp.localsearch import (
    DEFAULT_K,
    accelerated_search,
    naive_search,
    route_distance,
)
from vrp.model import TravelMatrix


def ring(size: int, seed: int = 0) -> TravelMatrix:
    """Points on a circle, so the optimal tour is known to be the ring order.

    A geometry with a knowable answer is worth more than a random one here:
    it lets the correctness tests check the search found something sensible
    rather than merely something self-consistent.
    """
    import math

    rng = __import__("random").Random(seed)
    points = []
    for i in range(size):
        angle = 2 * math.pi * i / size
        points.append((math.cos(angle) * 10_000 + rng.uniform(-50, 50),
                       math.sin(angle) * 10_000 + rng.uniform(-50, 50)))
    grid = tuple(
        tuple(round(math.hypot(points[i][0] - points[j][0],
                               points[i][1] - points[j][1]))
              for j in range(size))
        for i in range(size))
    return TravelMatrix(version=f"ring-{size}", durations=grid, distances=grid)


def shuffled(size: int, seed: int = 1) -> list[int]:
    order = list(range(1, size))
    __import__("random").Random(seed).shuffle(order)
    return [0, *order]


# --------------------------------------------------------------------------
# Correctness first
# --------------------------------------------------------------------------

@pytest.mark.parametrize("search", [naive_search, accelerated_search])
def test_the_result_is_a_permutation_of_the_input(search):
    """The cheapest way to be fast is to lose a stop. This is the guard."""
    matrix = ring(30)
    start = shuffled(30)
    result, _ = search(matrix, start)

    assert sorted(result) == sorted(start)
    assert result[0] == 0, "the route still begins at the depot"


@pytest.mark.parametrize("search", [naive_search, accelerated_search])
def test_the_result_is_never_worse_than_the_start(search):
    matrix = ring(40)
    start = shuffled(40)
    before = route_distance(matrix, start)
    result, _ = search(matrix, start)

    assert route_distance(matrix, result) <= before


def test_both_searches_improve_a_shuffled_ring_substantially():
    """On a ring the optimum is the ring order, so a shuffled start is far from
    it and any working local search should close most of the gap."""
    matrix = ring(40)
    start = shuffled(40)
    before = route_distance(matrix, start)
    optimum = route_distance(matrix, list(range(40)))

    for search in (naive_search, accelerated_search):
        after = route_distance(matrix, search(matrix, start)[0])
        closed = (before - after) / (before - optimum)
        assert closed > 0.5, f"{search.__name__} closed only {closed:.0%}"


def test_the_accelerated_search_reaches_a_true_local_optimum():
    """Granularity and don't-look bits are pruning, and pruning can hide an
    improving move. Checked exhaustively on an instance small enough to
    enumerate every relocate and 2-opt: none may improve on the result.
    """
    matrix = ring(18)
    result, _ = accelerated_search(matrix, shuffled(18))
    best = route_distance(matrix, result)

    for i in range(1, len(result)):
        for j in range(1, len(result)):
            if i == j:
                continue
            moved = result[:]
            moved.insert(j, moved.pop(i))
            assert route_distance(matrix, moved) >= best, (
                f"relocate {i}->{j} improves on the 'local optimum'")
    for i in range(1, len(result) - 1):
        for j in range(i + 2, len(result)):
            flipped = result[:i] + result[i:j][::-1] + result[j:]
            assert route_distance(matrix, flipped) >= best, (
                f"2-opt {i},{j} improves on the 'local optimum'")


def test_the_two_searches_agree_on_quality_within_a_few_percent():
    """Pruning must not cost much quality. They will not agree exactly -- they
    explore in a different order -- but a granular search that lost ten percent
    of the improvement would be trading the wrong thing for speed."""
    matrix = ring(60)
    start = shuffled(60)

    naive = route_distance(matrix, naive_search(matrix, start)[0])
    fast = route_distance(matrix, accelerated_search(matrix, start)[0])

    assert abs(fast - naive) / naive < 0.05, (naive, fast)


# --------------------------------------------------------------------------
# The measurement (T-33's acceptance)
# --------------------------------------------------------------------------

def _throughput(search, matrix, start) -> tuple[float, int, float]:
    """Evaluations per second, how many were made, and the wall clock.

    All three, because the rate alone is misleading in both directions. The
    accelerated search evaluates far *fewer* candidates -- granularity and
    don't-look bits prune them -- so a rate comparison credits it only for
    being quicker per candidate and not at all for needing fewer.
    """
    began = time.perf_counter()
    _, evaluations = search(matrix, start)
    elapsed = time.perf_counter() - began
    return evaluations / max(elapsed, 1e-9), evaluations, elapsed


def test_the_accelerated_search_is_at_least_ten_times_the_naive_throughput():
    """T-33's number, measured on a 120-node instance.

    Reported rather than merely asserted, so a regression shows how far it
    fell rather than only that it did. Three numbers, because they say
    different things: the *rate* is what T-33 asks for and credits only the
    O(1) delta, the *work* ratio credits granularity and the don't-look bits,
    and the *wall clock* is what an operator would actually notice. The last is
    two to three orders of magnitude and the first is around twelve times.
    """
    matrix = ring(120)
    start = shuffled(120)

    naive_rate, naive_evals, naive_time = _throughput(naive_search, matrix, start)
    fast_rate, fast_evals, fast_time = _throughput(accelerated_search, matrix, start)
    ratio = fast_rate / naive_rate

    print(f"\n  naive        {naive_rate:>12,.0f} eval/s  "
          f"{naive_evals:>10,} evals  {naive_time:>8.3f}s")
    print(f"  accelerated  {fast_rate:>12,.0f} eval/s  "
          f"{fast_evals:>10,} evals  {fast_time:>8.3f}s")
    print(f"  rate ratio   {ratio:>12.1f}x   (T-33 wants >= 10)")
    print(f"  work ratio   {naive_evals / fast_evals:>12.1f}x   fewer candidates")
    print(f"  wall clock   {naive_time / fast_time:>12.1f}x   faster overall")

    assert ratio >= 10, f"only {ratio:.1f}x"


def test_the_gap_widens_with_instance_size():
    """The naive cost is O(n) per evaluation and the accelerated one O(1), so
    the ratio must grow with n. A constant-factor speedup would pass the
    threshold test above on one size and mean something quite different.
    """
    ratios = {}
    for size in (40, 120):
        matrix, start = ring(size), shuffled(size)
        naive_rate, _, _ = _throughput(naive_search, matrix, start)
        fast_rate, _, _ = _throughput(accelerated_search, matrix, start)
        ratios[size] = fast_rate / naive_rate

    assert ratios[120] > ratios[40], ratios


def test_a_settled_route_costs_one_verification_sweep_and_no_more():
    """What don't-look bits actually buy, stated correctly.

    A search restarted from its own output cannot do "almost nothing" -- it has
    no memory of the previous run, so it must check every node once to learn
    there is nothing to do. An earlier version of this test asserted the second
    pass would be less than half the first, and it is not: it is one sweep,
    which for n=80 and k=20 is most of what a short first pass costs anyway.

    The property that holds is that it is *exactly* one sweep. Without the
    bits, a node whose neighbourhood is unchanged would be re-examined after
    every move elsewhere in the route, and settling would cost many sweeps.
    """
    size, k = 80, 20
    matrix = ring(size)
    settled, first_pass = accelerated_search(matrix, shuffled(size), k=k)
    _, second_pass = accelerated_search(matrix, settled, k=k)

    # Two candidate moves per (node, neighbour) pair, once each.
    one_sweep = size * (k + 1) * 2
    assert second_pass <= one_sweep, (second_pass, one_sweep)
    assert second_pass < first_pass, (first_pass, second_pass)


def test_the_bits_stop_a_settled_node_being_re_examined():
    """The mechanism directly: a route already at a local optimum queues every
    node once, and nothing wakes any of them again."""
    matrix = ring(40)
    settled, _ = accelerated_search(matrix, shuffled(40))
    _, evaluations = accelerated_search(matrix, settled)

    # If any node were re-woken, the count would exceed a single pass over the
    # granular neighbourhood.
    assert evaluations <= 40 * (DEFAULT_K + 1) * 2, evaluations


def test_the_dont_look_bits_cut_the_work_of_settling_a_route():
    """The bits measured where they act: during settling, not after it.

    Every other test here starts from or ends at a settled route, where no move
    fires and the bits therefore save nothing. Perturbation proved that -- waking
    every node after every move left all eleven green. Settling a shuffled ring
    is where the difference lives.
    """
    matrix = ring(80)
    start = shuffled(80)

    _, with_bits = accelerated_search(matrix, start, dont_look_bits=True)
    _, without = accelerated_search(matrix, start, dont_look_bits=False)

    print(f"\n  settling 80 nodes: {with_bits:,} evals with bits, "
          f"{without:,} without ({without / with_bits:.1f}x)")
    assert with_bits < without, (with_bits, without)
    assert without / with_bits > 1.5, (with_bits, without)
