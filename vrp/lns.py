"""Ruin-and-recreate: SISR — ALG-3b, T-34.

ALG-3b names three pieces and, unusually, says what each is *for*. That is what
makes them testable, and the reasons are worth keeping next to the code because
each piece has a plausible implementation that does nothing:

* **Adjacent string removal.** Remove short contiguous runs of visits that are
  near one another in space, across several routes. A removal that took
  scattered nodes would be random removal wearing this one's name, and would
  destroy exactly the route structure the operator exists to preserve.
* **Greedy insertion with blinks.** Insert greedily, but skip the best position
  with small probability. Blinks at probability zero are plain greedy; the
  parameter has to change the outcome or it is decoration.
* **Simulated annealing acceptance.** An annealer that never accepts a worse
  solution is hill-climbing with extra arithmetic.

The claim that justifies specifying SISR rather than any ruin operator is
comparative -- it beats random removal at equal budget -- and that is measured
in the tests rather than asserted here.

Placement: Python. Search internals, off the request path. The same note as
`vrp.localsearch` applies: if this became the production search rather than a
portfolio member under test, it is a tight numeric loop and would have a real
argument for Rust.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import pairwise

from vrp.model import TravelMatrix

# ALG-3b calls for "short" strings. The paper draws a length per string; this
# keeps the ceiling low so a removal stays a scalpel rather than a route
# deletion, which the spec lists as a separate operator.
#
# Measured, and worth knowing before tuning it: this parameter is nearly inert
# over its useful range. Mean length of the contiguous runs actually removed,
# on the clustered fixture at target 8:
#
#     MAX_STRING = 1  ->  2.00        MAX_STRING = 4  ->  2.22
#     MAX_STRING = 2  ->  2.00        MAX_STRING = 8  ->  3.29
#
# Runs of about two form even when the draw is forced to one, because the
# nearest-first anchoring and the singleton fallback below pick adjacent
# survivors on their own. So the contiguity ALG-3b asks for is real, but it
# comes from the *spatial* half of the operator rather than from this number --
# and the 11% advantage over random removal survives forcing this to 1. Anyone
# tuning SISR here should tune the anchoring first.
MAX_STRING = 4


def route_cost(matrix: TravelMatrix, route: list[int]) -> int:
    """Depot out, round the route, depot back."""
    if not route:
        return 0
    total = matrix.distance(0, route[0]) + matrix.distance(route[-1], 0)
    for a, b in pairwise(route):
        total += matrix.distance(a, b)
    return total


def plan_cost(matrix: TravelMatrix, plan: list[list[int]]) -> int:
    return sum(route_cost(matrix, route) for route in plan)


def sisr_ruin(matrix: TravelMatrix, plan: list[list[int]], target: int,
              rng: random.Random) -> tuple[list[list[int]], list[int]]:
    """Adjacent string removal. ALG-3b's ruin operator.

    Picks a seed customer, then repeatedly removes a short contiguous string
    from the route of a customer near that seed -- so the removals are adjacent
    *within* routes and close together *between* them. Both halves matter: the
    first preserves structure, the second is what induces the spatial slack the
    recreate step then exploits.

    Returns the surviving plan and the removed customers.
    """
    remaining = [route[:] for route in plan]
    customers = [node for route in remaining for node in route]
    if not customers:
        return remaining, []

    seed = rng.choice(customers)
    # Nearest first, so successive strings come from the same neighbourhood.
    by_distance = sorted(customers, key=lambda node: matrix.distance(seed, node))

    removed: list[int] = []
    used_routes: set[int] = set()
    for anchor in by_distance:
        if len(removed) >= target:
            break
        index = next((i for i, route in enumerate(remaining) if anchor in route),
                     None)
        if index is None or index in used_routes:
            continue
        route = remaining[index]
        position = route.index(anchor)
        length = min(rng.randint(1, MAX_STRING), len(route),
                     target - len(removed))
        # Centre the string on the anchor so the removal is a run around it
        # rather than a tail, which would bias towards route ends.
        begin = max(0, min(position - length // 2, len(route) - length))
        removed.extend(route[begin:begin + length])
        remaining[index] = route[:begin] + route[begin + length:]
        used_routes.add(index)

    # A pass that touched every route without reaching the target: take the
    # nearest survivors singly rather than returning short.
    if len(removed) < target:
        gone = set(removed)
        for anchor in by_distance:
            if len(removed) >= target:
                break
            if anchor in gone:
                continue
            for route in remaining:
                if anchor in route:
                    route.remove(anchor)
                    removed.append(anchor)
                    break

    return remaining, removed


def random_ruin(matrix: TravelMatrix, plan: list[list[int]], target: int,
                rng: random.Random) -> tuple[list[list[int]], list[int]]:
    """Random node removal. The baseline SISR is specified in preference to.

    Present so the comparison in the tests is against something real rather
    than a description of something.
    """
    remaining = [route[:] for route in plan]
    customers = [node for route in remaining for node in route]
    removed = rng.sample(customers, min(target, len(customers)))
    for node in removed:
        for route in remaining:
            if node in route:
                route.remove(node)
                break
    return remaining, removed


def greedy_recreate(matrix: TravelMatrix, plan: list[list[int]],
                    removed: list[int], blink: float,
                    rng: random.Random) -> list[list[int]]:
    """Greedy insertion with blinks. ALG-3b's recreate.

    Each customer goes to its cheapest insertion position, except that each
    candidate position is skipped with probability `blink`. Skipping the best
    position is what diversifies: the customer lands somewhere slightly worse,
    and the search explores a neighbourhood pure greedy would never reach.

    Deterministic when `blink` is zero, which the tests rely on -- if it were
    not, the blink probability would not be the only source of variation and
    would not be the knob it appears to be.
    """
    rebuilt = [route[:] for route in plan]
    for node in removed:
        best = None
        for index, route in enumerate(rebuilt):
            for position in range(len(route) + 1):
                if blink and rng.random() < blink:
                    continue
                candidate = route[:position] + [node] + route[position:]
                delta = route_cost(matrix, candidate) - route_cost(matrix, route)
                if best is None or delta < best[0]:
                    best = (delta, index, position)
        if best is None:
            # Every position blinked away. Put it back somewhere valid rather
            # than dropping it: this is a repair operator, not a filter.
            rebuilt[0].insert(0, node)
            continue
        _, index, position = best
        rebuilt[index].insert(position, node)
    return rebuilt


@dataclass(frozen=True)
class Acceptance:
    """Simulated annealing. ALG-3b's acceptance criterion.

    Temperature decays geometrically from start to end across the run, which is
    the standard schedule and the one the SISR paper uses. An improvement is
    always accepted; a worse candidate is accepted with probability
    exp(-delta / T), so early iterations explore and late ones converge.
    """

    start_temperature: float
    end_temperature: float
    iterations: int

    def temperature(self, iteration: int) -> float:
        if self.iterations <= 1:
            return self.end_temperature
        ratio = self.end_temperature / self.start_temperature
        return self.start_temperature * ratio ** (iteration / (self.iterations - 1))

    def __call__(self, current: float, candidate: float, iteration: int,
                 rng: random.Random) -> bool:
        if candidate <= current:
            return True
        temperature = max(self.temperature(iteration), 1e-9)
        return rng.random() < math.exp(-(candidate - current) / temperature)


_RUINS = {"sisr": sisr_ruin, "random": random_ruin}


def lns_search(matrix: TravelMatrix, plan: list[list[int]], iterations: int,
               seed: int = 0, ruin: str = "sisr",
               blink: float = 0.01) -> list[list[int]]:
    """Ruin, recreate, accept. ALG-3b's loop.

    Args:
        matrix: pinned travel data.
        plan: starting routes, customers only -- the depot is implicit.
        iterations: deterministic budget (CON-4: not wall-clock).
        seed: the run's seed. Same seed, same plan.
        ruin: "sisr" or "random". The second exists to be beaten.
        blink: probability of skipping a candidate insertion position.

    Returns:
        The best plan found, which is never worse than the one given.

    Raises:
        ValueError: unknown ruin operator, named rather than silently defaulted
            to SISR -- a typo that quietly ran the wrong operator would make
            every comparison between them meaningless.
    """
    if ruin not in _RUINS:
        raise ValueError(f"unknown ruin operator {ruin!r}; "
                         f"have {', '.join(sorted(_RUINS))}")
    destroy = _RUINS[ruin]
    rng = random.Random(seed)
    accept = Acceptance(start_temperature=max(plan_cost(matrix, plan) * 0.02, 1.0),
                        end_temperature=1.0, iterations=iterations)

    current = [route[:] for route in plan]
    current_cost = plan_cost(matrix, current)
    best, best_cost = [route[:] for route in current], current_cost

    served = sum(len(route) for route in plan)
    for iteration in range(iterations):
        target = max(1, min(rng.randint(1, 10), served - 1))
        remaining, removed = destroy(matrix, current, target, rng)
        candidate = greedy_recreate(matrix, remaining, removed, blink, rng)
        cost = plan_cost(matrix, candidate)

        if accept(current_cost, cost, iteration, rng):
            current, current_cost = candidate, cost
        if cost < best_cost:
            best, best_cost = [route[:] for route in candidate], cost

    return best
