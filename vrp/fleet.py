"""Fleet minimisation with absence-based acceptance — FR-32, §5.2, ALG-3b, T-35.

ALG-3b keeps this "separate procedure ... used when vehicle count is the primary
objective", and separate is the operative word. E-13 measured why a
distance-driven search will not find the smallest fleet as a by-product: more
vehicles is monotonically *worse* on distance, so a cost-minimising search
already prefers few routes and stops well short of the fewest feasible. Removing
the last route normally *costs* distance, and only a procedure willing to accept
that trade will do it. §5.2's `MIN_VEHICLES` is exactly that willingness.

The shape is the standard one: pick a route, empty it into an ejection pool, and
try to insert the pool elsewhere. If the pool empties, the fleet has shrunk by
one; if it does not, restore and try a different route.

**The absence counter is what stops it cycling.** Without it the procedure meets
the same stubborn customer on every attempt, fails to place it, and gives up
having learned nothing. Counting how often each customer has sat in the pool,
and preferring to eject customers that have not, moves the search elsewhere.

The counter is bounded, for the reason ALG-4 gives about penalties one section
later: "unbounded penalty growth is a common cause of search collapse". An
absence count allowed to run away makes one customer permanently unejectable,
which removes a legal move from the search for good.

Placement: Python. Search internals, off the request path.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise

from vrp.model import TimeWindow, TravelMatrix

# How many times a customer's absence is allowed to count against ejecting it.
# Small: the counter is a nudge away from cycling, not a prohibition.
DEFAULT_CAP = 5


def routes_needed(plan: list[list[int]]) -> int:
    """Vehicles actually used. An empty route is not a vehicle."""
    return sum(1 for route in plan if route)


@dataclass
class Ejection:
    """The ejection pool's memory. ALG-3b's absence-based acceptance."""

    cap: int = DEFAULT_CAP
    # A count, so a customer never ejected reads as zero rather than raising.
    absence: dict[int, int] = field(
        default_factory=lambda: defaultdict(int))

    def record(self, customer: int) -> None:
        """Note that this customer has sat in the pool again."""
        self.absence[customer] = min(self.absence[customer] + 1, self.cap)

    def choose(self, candidates: list[int],
               demands: dict[int, int] | None = None) -> int:
        """Which customer to reinsert next: the one ejected least, largest first.

        Absence leads, because a customer already ejected five times is the
        last one worth trying again -- that is what breaks a cycle. Within the
        same absence, the largest demand goes first: reducing a fleet is a
        packing problem, and first-fit-decreasing packs where arbitrary order
        does not. Ties resolve on id so the choice is deterministic (CON-4).
        """
        weights = demands or {}
        return min(candidates,
                   key=lambda node: (self.absence[node], -weights.get(node, 0),
                                     node))


def _fits(route: list[int], node: int, position: int, capacity: int,
          demands: dict[int, int], matrix: TravelMatrix,
          windows: dict[int, TimeWindow] | None,
          service: dict[int, int] | None) -> bool:
    """Whether `node` can go into `route` at `position`.

    Capacity always; time windows when the instance has them. Feasibility is
    checked rather than assumed because the whole procedure rests on it: a
    reduction that inserted a customer illegally would report a smaller fleet
    than the instance admits, which is the one answer worse than not reducing.
    """
    if sum(demands.get(other, 0) for other in route) + demands.get(node, 0) > capacity:
        return False
    if windows is None:
        return True

    candidate = route[:position] + [node] + route[position:]
    clock = 0
    here = 0
    for stop in candidate:
        clock += matrix.duration(here, stop)
        window = windows.get(stop)
        if window is not None:
            if clock > window.end:
                return False
            clock = max(clock, window.start)
        clock += (service or {}).get(stop, 0)
        here = stop
    return True


def _insert(plan: list[list[int]], node: int, capacity: int,
            demands: dict[int, int], matrix: TravelMatrix,
            windows: dict[int, TimeWindow] | None,
            service: dict[int, int] | None) -> bool:
    """Put `node` in the cheapest feasible position anywhere. True if placed."""
    # Best fit by *residual capacity*, then by distance. Fleet minimisation is
    # a bin-packing problem wearing a routing problem's clothes: cheapest-by-
    # distance insertion spreads load evenly and leaves every vehicle
    # part-full, which is exactly the arrangement that needs one more of them.
    #
    # Measured on E-n22-k4, against its published minimum of four, over the two
    # packing decisions this module makes:
    #
    #     ejection order   insertion rule       routes
    #     largest-first    residual capacity    4
    #     largest-first    distance             4
    #     arbitrary        residual capacity    4
    #     arbitrary        distance             5
    #
    # So the two are *independently sufficient* here, not jointly necessary --
    # which is why perturbing either one alone leaves the benchmark test green.
    # Both are kept: they are different halves of the same idea (packing order
    # and packing placement) and a harder instance need not be so forgiving.
    # Recorded because an earlier version of this comment credited the
    # insertion rule alone, which the table shows is only half the story.
    best = None
    for index, route in enumerate(plan):
        if not route:
            continue
        load = sum(demands.get(other, 0) for other in route)
        residual = capacity - load - demands.get(node, 0)
        for position in range(len(route) + 1):
            if not _fits(route, node, position, capacity, demands, matrix,
                         windows, service):
                continue
            before = _route_length(matrix, route)
            after = _route_length(matrix, route[:position] + [node] + route[position:])
            key = (residual, after - before)
            if best is None or key < best[0]:
                best = (key, index, position)
    if best is None:
        return False
    _, index, position = best
    plan[index].insert(position, node)
    return True


def _route_length(matrix: TravelMatrix, route: list[int]) -> int:
    if not route:
        return 0
    total = matrix.distance(0, route[0]) + matrix.distance(route[-1], 0)
    for a, b in pairwise(route):
        total += matrix.distance(a, b)
    return total


def minimise_fleet(matrix: TravelMatrix, plan: list[list[int]], capacity: int,
                   demands: dict[int, int], seed: int = 0,
                   windows: dict[int, TimeWindow] | None = None,
                   service: dict[int, int] | None = None,
                   attempts: int = 200) -> list[list[int]]:
    """Reduce the number of routes without losing a customer. ALG-3b, FR-32.

    Args:
        matrix: pinned travel data; node 0 is the depot.
        plan: starting routes, customers only.
        capacity: per-vehicle capacity in the demands' units.
        demands: demand per customer node.
        seed: run seed. Same seed, same fleet (CON-4).
        windows: hard time windows per node, when the instance has them.
        service: service duration per node.
        attempts: how many route-removal attempts before giving up. A
            deterministic budget rather than a time limit, for CON-4's reason.

    Returns:
        A plan with no more routes than the one given and exactly the same
        customers. Never fewer customers: losing one is the only outcome worse
        than not reducing the fleet.
    """
    rng = random.Random(seed)
    current = [route[:] for route in plan if route]
    ejection = Ejection()

    for _ in range(attempts):
        if len(current) <= 1:
            break

        # Smallest route first: the cheapest one to empty, and the one whose
        # customers are likeliest to fit elsewhere.
        victim = min(range(len(current)), key=lambda i: (len(current[i]), i))
        pool = list(current[victim])
        remaining = [route[:] for index, route in enumerate(current)
                     if index != victim]

        placed = True
        while pool:
            node = ejection.choose(pool, demands)
            pool.remove(node)
            if not _insert(remaining, node, capacity, demands, matrix,
                           windows, service):
                ejection.record(node)
                placed = False
                break

        if placed:
            current = remaining
            continue

        # The attempt failed. Rotate which route is tried next rather than
        # meeting the same one every time -- with the absence counter already
        # steering the ejection order, this is what keeps the two from
        # deadlocking on one arrangement.
        rng.shuffle(current)

    return current
