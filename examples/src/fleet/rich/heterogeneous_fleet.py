"""Six depots, mixed vehicles: the shape this business actually has.

Demonstrates the heterogeneous-fleet work landed for E-21/T-21 against the
Costa Rica dataset and real road distances:

    vrp.model      per-vehicle capacity, cost and open routes (FR-07, FR-08)
    vrp.osrm       a pinned matrix from the gateway
    vrp.solve      PyVRP, with the costs the vehicles actually carry
    vrp.verify     the independent verifier

SDD §3.4 calls **MDHVRPTW** the target shape for this business and is blunt
about why: "six depots, mixed vehicles, and customer windows is not an exotic
combination — it is the ordinary case. Anything that treats the fleet as
homogeneous or the depot as singular is a stepping stone, not a deliverable."
The dataset ships those six depots, so this runs the real thing.

Three things it shows:

1. **Cost belongs to the vehicle.** A rigid and a van do not cost the same per
   kilometre, and before E-21 they did — costs lived on `ObjectiveSpec`, one
   set for the whole fleet, which made the "H" in MDHVRPTW decorative. Swap
   which vehicle is dear and the plan changes.

2. **Vehicles start where they are.** Each depot has its own vehicle, and the
   solver assigns work to whichever is genuinely closer by road rather than by
   straight line.

3. **Open routes do not pay for the leg home.** A subcontractor finishing at
   their last drop drives one way. `end_location_id=None` used to mean "return
   to the start", so that leg was charged whether or not anyone drove it.

Requires a running gateway; `examples/.env` points at the FreeBSD jail.
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/heterogeneous_fleet.py --stops 24
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

# Importing config puts OSRM_API_URL into the environment and the repository
# root on sys.path, which is what makes `import vrp` below resolve.
import config  # noqa: F401
import dataset

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
SHIFT = TimeWindow(start=6 * 3600, end=20 * 3600)


@dataclass(frozen=True)
class VehicleClass:
    """A vehicle type as a haulier would describe it, in whole colones."""

    name: str
    capacity_kg: int
    fixed_cost: int          # putting it on the road for the day
    cost_per_metre: int      # fuel, tyres, wear


# Deliberately different on both axes: the rigid costs more to deploy and more
# to drive, and carries four times as much. A fleet where one class dominates
# on every axis would make the assignment obvious and prove nothing.
FLEET = (
    VehicleClass("VAN", capacity_kg=1_200, fixed_cost=35_000, cost_per_metre=1),
    VehicleClass("RIGID", capacity_kg=5_000, fixed_cost=90_000, cost_per_metre=3),
)


def build(depots: list[dict], deliveries: list[dict], matrix: TravelMatrix,
          *, open_routes: bool, flip_costs: bool = False) -> Problem:
    """One vehicle of each class per depot, over the given stops."""
    locations = [Location(id=d["name"], lat=d["latitude"], lon=d["longitude"],
                          matrix_index=i)
                 for i, d in enumerate(depots)]
    orders = []
    for offset, delivery in enumerate(deliveries):
        index = len(depots) + offset
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"], matrix_index=index))
        orders.append(Order(
            id=delivery["product_id"], kind="JOB",
            quantities={"kg": max(1, round(delivery["weight_kg"]))},
            delivery=StopSpec(location_id=delivery["product_id"],
                              time_windows=(SHIFT,),
                              service_fixed=delivery["service_minutes"] * 60),
        ))

    vehicles = []
    for depot in depots:
        for spec in FLEET:
            # Flipping swaps which class is dear, which is how the example
            # shows the costs are read rather than assumed.
            other = FLEET[1] if spec is FLEET[0] else FLEET[0]
            costs = other if flip_costs else spec
            vehicles.append(Vehicle(
                id=f"{spec.name}@{depot['name'].split()[0]}",
                capacities={"kg": spec.capacity_kg},
                shift=SHIFT,
                start_location_id=depot["name"],
                end_location_id=None if open_routes else depot["name"],
                open_route=open_routes,
                fixed_cost=costs.fixed_cost,
                cost_per_metre=costs.cost_per_metre,
            ))

    return Problem(id="mdhvrptw", locations=tuple(locations),
                   orders=tuple(orders), vehicles=tuple(vehicles), matrix=matrix)


def travelled(problem: Problem, solution) -> int:
    index = {location.id: location.matrix_index for location in problem.locations}
    total = 0
    for route in solution.routes:
        ids = [step.location_id for step in route.steps]
        total += sum(problem.matrix.distance(index[a], index[b])
                     for a, b in pairwise(ids))
    return total


def money(problem: Problem, solution) -> int:
    """What the plan costs, using each vehicle's own rates."""
    index = {location.id: location.matrix_index for location in problem.locations}
    total = 0
    for route in solution.routes:
        if not any(step.order_id for step in route.steps):
            continue
        vehicle = problem.vehicle(route.vehicle_id)
        ids = [step.location_id for step in route.steps]
        metres = sum(problem.matrix.distance(index[a], index[b])
                     for a, b in pairwise(ids))
        total += vehicle.fixed_cost + metres * vehicle.cost_per_metre
    return total


def report(label: str, problem: Problem, solution) -> None:
    used = [r for r in solution.routes if any(s.order_id for s in r.steps)]
    verdict = verify(problem, solution)
    print(f"\n  {label}")
    print(f"    status        {solution.status}, verifier "
          f"{'accepts' if verdict.ok else 'REJECTS'}")
    if not verdict.ok:
        for violation in verdict.violations[:2]:
            print(f"      {violation}")
    print(f"    vehicles      {len(used)} of {len(problem.vehicles)}")
    print(f"    distance      {travelled(problem, solution):,} m")
    print(f"    cost          {money(problem, solution):,}")
    for route in sorted(used, key=lambda r: r.vehicle_id):
        stops = sum(1 for s in route.steps if s.order_id)
        print(f"      {route.vehicle_id:<26} {stops:>2} stops  "
              f"ends {route.steps[-1].location_id[:22]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", type=int, default=24)
    parser.add_argument("--iterations", type=int, default=1500)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()


    deliveries, depots = dataset.load(args.dataset).around_each_depot(args.stops)
    print(f"{len(depots)} depots, {len(deliveries)} stops, "
          f"{len(depots) * len(FLEET)} vehicles "
          f"({', '.join(v.name for v in FLEET)} at each depot)")
    print(f"fetching a road matrix from {GATEWAY}")

    points = [(d["latitude"], d["longitude"]) for d in depots]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    matrix, snaps = build_matrix(GATEWAY, points)
    furthest = max(snaps, key=lambda s: s.distance_m)
    print(f"snapping: worst {furthest.distance_m:.0f} m ({furthest.name or 'unnamed road'})")

    print(f"\nsolving ({args.iterations} iterations)")
    closed = build(depots, deliveries, matrix, open_routes=False)
    report("closed routes, van cheap to run", closed,
           solve(closed, iterations=args.iterations, seed=0))

    flipped = build(depots, deliveries, matrix, open_routes=False, flip_costs=True)
    report("closed routes, costs swapped", flipped,
           solve(flipped, iterations=args.iterations, seed=0))

    opened = build(depots, deliveries, matrix, open_routes=True)
    report("open routes (no leg home)", opened,
           solve(opened, iterations=args.iterations, seed=0))

    print("\n" + "=" * 72)
    print("The three plans differ because the vehicles do. Swapping which class")
    print("is dear changes which is deployed -- the fleet is read, not assumed --")
    print("and an open route stops at its last drop instead of driving home.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
