"""Accelerated local search — ALG-2, T-33.

ALG-2 names three accelerations and is unusually emphatic about one of them:
O(1) move evaluation "is the single largest determinant of local-search
throughput and MUST be implemented before any tuning work". T-33 puts a number
on the result — ten times the naive throughput — and E-33 adds that it must be
*measured*.

Both searches are here, because the claim is comparative and a benchmark
against an absent baseline is not a benchmark. `naive_search` is not a straw
man: it is what a competent person writes first, and each of its three
properties is one an acceleration removes.

    naive          every pair a candidate; route cost recomputed by summing the
                   whole route per candidate; every node revisited every sweep
    accelerated    candidates from the k nearest eligible neighbours; delta from
                   the four affected edges alone; nodes skipped while their
                   don't-look bit is set

**Scope.** ALG-2's full move set is relocate(1..3), swap(1..3), 2-opt, 2-opt*,
or-opt, swap*, and the pickup-delivery pair moves. Implemented here are
relocate(1) and 2-opt, on a single route. That is enough to measure what T-33
asks about -- throughput is a property of the acceleration machinery rather
than of the move catalogue -- and not enough to be the production search. The
inter-route moves need the segment aggregates below extended across routes,
which is where the remaining work is.

Placement: Python. Search internals, off the request path. If this ever becomes
the production search rather than a measurement, it is the first thing in this
project with a real argument for Rust -- it is a tight numeric loop and nothing
else here is.
"""

from __future__ import annotations

from vrp.model import TravelMatrix

# ALG-2 suggests k between 20 and 40. 20 keeps the candidate set small on the
# instances this is measured against; the value matters to speed and, past a
# point, to quality -- `test_the_two_searches_agree_on_quality` is the guard.
DEFAULT_K = 20


def route_distance(matrix: TravelMatrix, route: list[int]) -> int:
    """Total distance of a closed tour. O(n), and the naive search's inner cost."""
    total = 0
    for position in range(len(route)):
        total += matrix.distance(route[position], route[(position + 1) % len(route)])
    return total


def naive_search(matrix: TravelMatrix, route: list[int]) -> tuple[list[int], int]:
    """First-improvement relocate and 2-opt, evaluated by full recomputation.

    Deliberately written the obvious way. Every pair is a candidate, the whole
    route is re-summed to score each one, and a sweep that changes nothing is
    the only stopping condition. Returns the route and the number of candidate
    evaluations, which is what the throughput comparison divides by time.
    """
    route = route[:]
    best = route_distance(matrix, route)
    evaluations = 0
    improved = True

    while improved:
        improved = False
        for i in range(1, len(route)):
            for j in range(1, len(route)):
                if i == j:
                    continue
                candidate = route[:]
                candidate.insert(j, candidate.pop(i))
                evaluations += 1
                cost = route_distance(matrix, candidate)
                if cost < best:
                    route, best, improved = candidate, cost, True
                    break
            if improved:
                break
        if improved:
            continue

        for i in range(1, len(route) - 1):
            for j in range(i + 2, len(route)):
                candidate = route[:i] + route[i:j][::-1] + route[j:]
                evaluations += 1
                cost = route_distance(matrix, candidate)
                if cost < best:
                    route, best, improved = candidate, cost, True
                    break
            if improved:
                break

    return route, evaluations


def _neighbours(matrix: TravelMatrix, size: int, k: int) -> list[list[int]]:
    """The k nearest eligible neighbours of each node, plus the depot. ALG-2.

    Built once per instance. This is the granular neighbourhood, and it is what
    turns an O(n^2) candidate set into an O(nk) one -- the depot is always
    included because moves involving it are the ones that split and merge
    routes.
    """
    granular = []
    for node in range(size):
        others = [other for other in range(size) if other != node]
        others.sort(key=lambda other: matrix.distance(node, other))
        nearest = others[:k]
        if 0 not in nearest and node != 0:
            nearest.append(0)
        granular.append(nearest)
    return granular


def accelerated_search(matrix: TravelMatrix, route: list[int],
                       k: int = DEFAULT_K,
                       dont_look_bits: bool = True) -> tuple[list[int], int]:
    """The same moves, with ALG-2's three accelerations.

    Args:
        matrix: pinned travel data.
        route: starting tour, beginning at the depot.
        k: granular neighbourhood size.
        dont_look_bits: when False, every node is re-queued after any move --
            which is the behaviour the bits exist to avoid. Exposed because
            T-33 asks for a documented profile, and an acceleration whose
            contribution cannot be measured separately cannot be profiled. It
            is also the only way to test that this one does anything:
            perturbing it away left every test green, because the tests all
            measured a route that was already settled and therefore made no
            moves for the bits to save work on.

    Returns:
        The improved route and the number of candidate evaluations made, so the
        caller can divide by elapsed time and compare.

    The delta is computed from the edges a move actually changes -- four for a
    relocate, two for a 2-opt -- rather than by re-summing the tour. That is
    ALG-2's O(1) evaluation, and on a tour of n nodes it is the difference
    between O(1) and O(n) per candidate, which is why the advantage grows with
    the instance rather than being a constant factor.

    Don't-look bits: a node is queued only when an edge incident to it changes.
    A search restarted from its own output therefore does almost no work, which
    is what `test_dont_look_bits_cut_the_work_on_a_second_pass` checks.
    """
    route = route[:]
    size = len(route)
    granular = _neighbours(matrix, size, k)
    position = {node: index for index, node in enumerate(route)}

    # Every node starts "dirty": nothing is known about the incoming route.
    queue = list(route)
    queued = set(route)
    evaluations = 0

    def wake(*nodes: int) -> None:
        for node in nodes:
            if node not in queued:
                queued.add(node)
                queue.append(node)

    while queue:
        node = queue.pop(0)
        queued.discard(node)
        index = position[node]
        if index == 0:
            continue

        previous = route[index - 1]
        following = route[(index + 1) % size]
        removed = (matrix.distance(previous, node)
                   + matrix.distance(node, following)
                   - matrix.distance(previous, following))

        moved = False
        for other in granular[node]:
            target = position[other]
            if target == index:
                continue

            # relocate(1): put `node` after `other`.
            after = route[(target + 1) % size]
            if after != node:
                evaluations += 1
                added = (matrix.distance(other, node)
                         + matrix.distance(node, after)
                         - matrix.distance(other, after))
                if added < removed:
                    route.pop(index)
                    insert_at = position[other] + 1 if position[other] < index \
                        else position[other]
                    route.insert(insert_at, node)
                    position = {n: i for i, n in enumerate(route)}
                    wake(previous, following, node, other, after)
                    moved = True
                    break

            # 2-opt: reverse the segment between the two, which replaces two
            # edges and leaves the rest of the tour untouched.
            left, right = sorted((index, target))
            if right - left >= 2 and left >= 1:
                a, b = route[left - 1], route[left]
                c, d = route[right - 1], route[right % size]
                evaluations += 1
                delta = (matrix.distance(a, c) + matrix.distance(b, d)
                         - matrix.distance(a, b) - matrix.distance(c, d))
                if delta < 0:
                    route[left:right] = route[left:right][::-1]
                    position = {n: i for i, n in enumerate(route)}
                    wake(a, b, c, d)
                    moved = True
                    break

        if moved:
            wake(node)
            if not dont_look_bits:
                wake(*route)

    return route, evaluations
