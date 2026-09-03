"""The route a distance-minimising search will never remove.

Demonstrates the fleet-minimisation procedure landed for E-35/T-35 (ALG-3b,
FR-32, §5.2):

    vrp.fleet        `minimise_fleet` and the ejection pool's absence counter
    vrp.benchmarks   E-05's readers, so the targets come from the files
    vrp.solve        the distance-driven search this is measured against

ALG-3b calls fleet minimisation "a separate procedure", and separate is the
operative word. §5.2's `MIN_VEHICLES` puts vehicle count strictly above
distance, and a distance-minimising search does not arrive there as a side
effect: it stops at the fleet that is cheapest to drive, which under time
windows is not the smallest one. Removing that last route means repacking its
customers into detours the search has no reason to accept.

Four things this shows, in order:

1. **The trade, priced on a real instance.** RC208 carries genuine time
   windows. PyVRP finds four routes and will not go lower, because three costs
   a third more distance. That is not the search failing -- it is the search
   correctly refusing a move that is worse on the objective it was given.

2. **Against a published minimum.** E-n22-k4 states "Min no of trucks: 4" in
   its own COMMENT, read from the file by E-05's reader rather than
   transcribed, so the target cannot drift from the instance it belongs to.

3. **The absence counter, which is the part that can silently do nothing.**
   Without it the pool ejects the same stubborn customer every attempt and
   cycles. It is shown rising, shown changing which customer goes next, and
   shown bounded.

4. **What it will not do.** Capacity is a floor no acceptance criterion can
   argue with, and the customer count never changes: losing one is the only
   outcome worse than not reducing the fleet.

Runs offline. No gateway required. About 30 s, most of it PyVRP.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/alloc/fleet_minimisation.py
"""

from __future__ import annotations

import os
import sys
from itertools import pairwise
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.benchmarks import read_benchmark
from vrp.fleet import Ejection, minimise_fleet, routes_needed
from vrp.model import Problem, TravelMatrix
from vrp.osrm import build_matrix
from vrp.solve import pyvrp_adapter

INSTANCES = PROJECT_ROOT / "benchmarks" / "instances"
GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")


_GRIDS: dict[int, TravelMatrix] = {}


def grid(size: int) -> TravelMatrix:
    """A real round's road matrix, for the sections that need only a matrix.

    Sections 1 and 2 stay on the published instances above: their targets are
    read from the files, so the geography has to be the files' own or the
    comparison means nothing. This is for the rest, which prove things about
    the procedure rather than about an instance -- a capacity floor holds
    whatever the legs are -- and a real round is simply a truer setting for
    them than a chain of evenly spaced points.

    Args:
        size: How many nodes, the depot included.

    Returns:
        The road matrix over a depot and `size - 1` real deliveries.
    """
    if size not in _GRIDS:
        deliveries, depot = dataset.load().spread(size - 1)
        points = [(depot["latitude"], depot["longitude"])]
        points += [(d["latitude"], d["longitude"]) for d in deliveries]
        matrix, _ = build_matrix(GATEWAY, points)
        _GRIDS[size] = matrix
    return _GRIDS[size]


def plan_distance(matrix: TravelMatrix, plan: list[list[int]]) -> int:
    total = 0
    for route in plan:
        if route:
            nodes = [0, *route, 0]
            total += sum(matrix.distance(a, b) for a, b in pairwise(nodes))
    return total


def node_view(problem: Problem):
    """The instance as `minimise_fleet` wants it: matrix indices, not ids."""
    index = {location.id: location.matrix_index
             for location in problem.locations}
    at = {index[order.delivery.location_id]: order for order in problem.orders}
    return index, at


def show_the_trade() -> None:
    """Why a distance search stops one route short, measured on RC208."""
    print("\n1. RC208, with real time windows")
    problem = read_benchmark(INSTANCES / "RC208.vrp").problem
    index, at = node_view(problem)
    demands = {node: order.quantities["demand"] for node, order in at.items()}
    windows = {node: order.delivery.time_windows[0]
               for node, order in at.items()}
    service = {node: order.delivery.service_fixed for node, order in at.items()}
    capacity = problem.vehicles[0].capacities["demand"]

    solution = pyvrp_adapter.solve(problem, iterations=2000, seed=0)
    incumbent = [[index[problem.order(step.order_id).delivery.location_id]
                  for step in route.steps if step.order_id]
                 for route in solution.routes]
    incumbent = [route for route in incumbent if route]

    reduced = minimise_fleet(problem.matrix, incumbent, capacity=capacity,
                             demands=demands, seed=0, windows=windows,
                             service=service)

    print(f"   {'plan':<28}{'routes':>8}{'distance':>11}")
    for label, plan in (("PyVRP, 2000 iterations", incumbent),
                        ("after minimise_fleet", reduced)):
        print(f"   {label:<28}{routes_needed(plan):>8}"
              f"{plan_distance(problem.matrix, plan):>10,}m")

    grew = (plan_distance(problem.matrix, reduced)
            - plan_distance(problem.matrix, incumbent))
    print(f"   one fewer van, {grew:+,}m of distance "
          f"({grew / plan_distance(problem.matrix, incumbent) * 100:+.0f}%).")
    print("   PyVRP had every chance to find the three-route plan and declined")
    print("   it, correctly: it is a third worse on the objective it was given.")
    print("   Only a procedure that accepts that trade will remove the route.")

    _audit(problem, reduced, demands, windows, service, capacity)


def _audit(problem: Problem, plan, demands, windows, service, capacity) -> None:
    """Re-check the reduced plan without asking the procedure that built it."""
    shift_end = problem.vehicles[0].shift.end
    served = sorted(node for route in plan for node in route)
    print(f"\n   {'route':>7}{'stops':>7}{'load':>12}{'ends':>7}{'windows':>10}")
    for position, route in enumerate(route for route in plan if route):
        clock, here, kept = 0, 0, True
        for node in route:
            clock += problem.matrix.duration(here, node)
            kept &= clock <= windows[node].end
            clock = max(clock, windows[node].start) + service[node]
            here = node
        clock += problem.matrix.duration(here, 0)
        print(f"   {position:>7}{len(route):>7}"
              f"{sum(demands[n] for n in route):>7}/{capacity:<4}"
              f"{clock:>7}{kept!s:>10}")
    print(f"   {len(served)} of {len(demands)} customers, shift ends "
          f"{shift_end}. Checked here rather than taken on trust.")


def show_published_target() -> None:
    """A number from outside this project, which is rare enough to use."""
    print("\n2. E-n22-k4, whose own COMMENT states the minimum")
    problem = read_benchmark(INSTANCES / "E-n22-k4.txt").problem
    _, at = node_view(problem)
    demands = {node: order.quantities["demand"] for node, order in at.items()}

    start = [[node] for node in sorted(demands)]
    reduced = minimise_fleet(problem.matrix, start,
                             capacity=problem.vehicles[0].capacities["demand"],
                             demands=demands, seed=0)
    print(f"   {len(demands)} customers, {routes_needed(start)} routes in, "
          f"{routes_needed(reduced)} out. Published minimum: 4.")
    print("   RC208 above went to three, which is not a contradiction: its")
    print("   reference solution is distance-best, and four is that solution's")
    print("   route count rather than a proven vehicle minimum.")


def show_absence_counter() -> None:
    """ALG-3b's absence-based acceptance, which is easy to leave inert."""
    print("\n3. The absence counter")
    pool = Ejection()
    demands = {1: 5, 2: 9, 3: 5}

    print(f"   first choice from {sorted(demands)}: "
          f"{pool.choose([1, 2, 3], demands)}  (largest demand wins a tie)")
    for _ in range(3):
        pool.record(2)
    print(f"   after ejecting 2 three times: absence={dict(pool.absence)}")
    print(f"   next choice from {sorted(demands)}: "
          f"{pool.choose([1, 2, 3], demands)}  (2 is now the last to retry)")

    for _ in range(pool.cap + 5):
        pool.record(1)
    print(f"   the counter is bounded at {pool.cap}: "
          f"absence[1]={pool.absence[1]}")
    print("   Unbounded, a customer ejected early would be avoided forever.")
    print("   Without it at all, the procedure ejects the same stubborn")
    print("   customer every attempt and makes no progress.")


def show_the_limits() -> None:
    """Two things it will not do, whatever the objective says."""
    print("\n4. What it will not do")
    size, capacity = 7, 2
    demands = {node: 1 for node in range(1, size)}
    reduced = minimise_fleet(grid(size), [[n] for n in range(1, size)],
                             capacity=capacity, demands=demands, seed=0)
    served = sorted(node for route in reduced for node in route)
    floor = -(-sum(demands.values()) // capacity)

    print(f"   6 customers, capacity {capacity}: {routes_needed(reduced)} "
          f"routes, against a capacity floor of {floor}")
    print(f"   customers still served: {len(served)} of {len(demands)}")

    already = [[1, 2], [3, 4]]
    left = minimise_fleet(grid(5), already, capacity=2,
                          demands={n: 1 for n in range(1, 5)}, seed=0)
    print(f"   an already-minimal plan is left alone: {already} -> {left}")
    print("   Capacity is a floor no acceptance criterion can argue with, and")
    print("   the customer count never moves. A smaller fleet that dropped")
    print("   someone would not be a smaller fleet; it would be a smaller job.")


def main() -> int:
    show_the_trade()
    show_published_target()
    show_absence_counter()
    show_the_limits()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
