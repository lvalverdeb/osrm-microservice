"""When can this stop be served, and what does being late actually cost?

Demonstrates the time-window work landed for E-23/T-23 against the Costa Rica
dataset and real road distances:

    vrp.model      hard and soft windows, disjoint windows, release times
    vrp.evaluator  earliness and lateness costed asymmetrically
    vrp.solve      PyVRP, with disjoint windows as mutually-exclusive groups
    vrp.verify     the independent verifier

§6.2: "Multiple disjoint windows per stop, each hard or soft with asymmetric
earliness / lateness costs. Waiting is permitted (arrive early, wait) and MUST
be costed explicitly, because uncosted waiting produces plans that look cheap
and consume the whole driver day."

Four scenarios over the same round:

1. **One wide window.** The baseline: no window pressure at all.
2. **Split windows.** Every stop takes deliveries in the morning *or* the late
   afternoon, not between. The solver must pick one per stop.
3. **Hard and narrow.** One tight morning window that the far stops cannot
   reach. Every order is required (no prize, so `T-27`'s optional orders do not
   apply), so the solver cannot drop anything: it returns its best *infeasible*
   plan, with arrivals clamped to the window, and the verifier rejects it on
   INV-4 for the timeline being impossible against the matrix. That chain —
   solver says INFEASIBLE, verifier says why — is the honest answer, and the
   stop count it prints is what the solver attempted, not what is achievable.
4. **Soft and narrow.** The same tight window, made soft. Every stop is served,
   some late, and the lateness is priced.

Scenario 4 is the one worth watching. Before E-23 the model accepted a soft
window and the adapter passed it to the solver as a hard bound, so this case
came back INFEASIBLE — refusing a plan any dispatcher would call "late".

Not delivered, and stated rather than implied: PyVRP has no soft time windows,
so it does not *search* for the cheapest lateness. A soft window becomes a wide
hard one and the breach is priced afterwards. The plan is legal and the cost is
honest; it is not optimal in the penalty.

Requires a running gateway; `examples/.env` points at the FreeBSD jail.
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/tw/multiple_windows.py --stops 14
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Importing config puts OSRM_API_URL into the environment and the repository
# root on sys.path, which is what makes `import vrp` below resolve.
import config  # noqa: F401
import dataset

from vrp.evaluator import ObjectiveWeights, evaluate
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.osrm import build_matrix
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DATASET = dataset.DEFAULT_PATH
HOUR = 3600
SHIFT = TimeWindow(start=6 * HOUR, end=20 * HOUR)

# A home delivery an hour early is an inconvenience; an hour late is a failed
# delivery and a second visit. §6.2 asks for exactly this asymmetry.
EARLINESS_PER_SEC = 1
LATENESS_PER_SEC = 12


def windows_for(scenario: str, index: int) -> tuple[TimeWindow, ...]:
    """The window topology each scenario puts on stop `index`."""
    if scenario == "wide":
        return (SHIFT,)
    if scenario == "split":
        # Morning or late afternoon, never between: two disjoint windows.
        return (TimeWindow(start=7 * HOUR, end=11 * HOUR),
                TimeWindow(start=16 * HOUR, end=19 * HOUR))
    tight = TimeWindow(
        start=7 * HOUR, end=8 * HOUR,
        hardness="SOFT" if scenario == "soft" else "HARD",
        earliness_cost_per_sec=EARLINESS_PER_SEC if scenario == "soft" else 0,
        lateness_cost_per_sec=LATENESS_PER_SEC if scenario == "soft" else 0)
    return (tight,)


def build(depot: dict, deliveries: list[dict], matrix: TravelMatrix,
          scenario: str) -> Problem:
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for offset, delivery in enumerate(deliveries):
        index = offset + 1
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"], matrix_index=index))
        orders.append(Order(
            id=delivery["product_id"], kind="JOB",
            quantities={"kg": dataset.load_kg(delivery)},
            delivery=StopSpec(location_id=delivery["product_id"],
                              time_windows=windows_for(scenario, offset),
                              service_fixed=delivery["service_minutes"] * 60),
        ))
    total = sum(o.quantities["kg"] for o in orders)
    return Problem(
        id=f"tw-{scenario}", locations=tuple(locations), orders=tuple(orders),
        vehicles=(Vehicle(id="VAN-1", capacities={"kg": total}, shift=SHIFT,
                          start_location_id="DEPOT", end_location_id="DEPOT"),),
        matrix=matrix)


def clock(seconds: int) -> str:
    return f"{seconds // HOUR:02d}:{seconds % HOUR // 60:02d}"


def report(label: str, problem: Problem, solution) -> None:
    verdict = verify(problem, solution)
    served = [s for route in solution.routes for s in route.steps if s.order_id]
    assignment = {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
                  for route in solution.routes}
    evaluation = evaluate(problem, assignment, ObjectiveWeights())

    print(f"\n  {label}")
    print(f"    status        {solution.status}, verifier "
          f"{'accepts' if verdict.ok else 'REJECTS'}")
    if not verdict.ok:
        for violation in verdict.violations[:2]:
            print(f"      {violation}")
    print(f"    served        {len(served)} of {len(problem.orders)}"
          + (f"   ({len(solution.unassigned)} unserved)"
             if solution.unassigned else ""))
    if served:
        print(f"    service from  {clock(min(s.start_service for s in served))}"
              f" to {clock(max(s.start_service for s in served))}")
    early = evaluation.breakdown["earliness_penalty"]
    late = evaluation.breakdown["lateness_penalty"]
    if early or late:
        print(f"    earliness     {early:,}   lateness {late:,}"
              f"   (rates {EARLINESS_PER_SEC} / {LATENESS_PER_SEC} per second)")
    print(f"    waiting       {evaluation.breakdown['waiting_seconds'] // 60} min")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", type=int, default=14)
    parser.add_argument("--iterations", type=int, default=1200)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()

    deliveries, depot = dataset.load(args.dataset).nearest(args.stops)
    print(f"depot {depot['name']} -- {len(deliveries)} stops")
    print(f"fetching a road matrix from {GATEWAY}")
    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    matrix, _ = build_matrix(GATEWAY, points)

    print(f"\nsolving four window topologies ({args.iterations} iterations each)")
    for scenario, label in (
        ("wide", "one wide window (06:00-20:00)"),
        ("split", "disjoint windows (07:00-11:00 or 16:00-19:00)"),
        ("hard", "hard and narrow (07:00-08:00)"),
        ("soft", "soft and narrow (07:00-08:00, lateness priced)"),
    ):
        problem = build(depot, deliveries, matrix, scenario)
        report(label, problem, solve(problem, iterations=args.iterations, seed=0))

    print("\n" + "=" * 72)
    print("The narrow hard window cannot be met, and nothing pretends otherwise:")
    print("the solver reports INFEASIBLE and the verifier names the impossible")
    print("arrival. The same window made soft serves every stop and prices the")
    print("lateness instead. Before E-23 the soft case reported INFEASIBLE too,")
    print("because the adapter passed a soft bound to the solver as a hard one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
