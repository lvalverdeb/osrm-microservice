"""Build a delivery plan from real data, evaluate it, and verify it independently.

Demonstrates the three modules landed for E-01/E-02/E-03 against the Costa Rica
dataset and real road distances:

    vrp.model      the problem, stated in whole seconds and whole units
    vrp.evaluator  the canonical timeline and objective
    vrp.verify     the independent verifier, which recomputes both from scratch

The plan itself is built by a plain nearest-neighbour construction. That is not
a solver and is not pretending to be one -- there is no solver in this
repository yet (see docs/planning/VRP_SDD_FIT_GAP.md). It exists to produce a
*plausible plan the verifier did not produce*, which is the only situation in
which an independent verifier means anything.

The last section is the point of the whole exercise: the same plan is reported
with a subtly wrong objective, and the verifier catches it. Objective drift
between a solver's incremental evaluator and ground truth is the failure the SDD
calls the source of most silent optimisation bugs, and it is invisible to every
check except this one.

Requires a running gateway with an engine behind it:
    make compose-up                      # or the jail, or a local pair
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/verify_delivery_plan.py --stops 40 --province "San Jose"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Importing config puts OSRM_API_URL into the environment and the repository
# root on sys.path, which is what makes `import vrp` below resolve.
import config  # noqa: F401
import httpx

from vrp.evaluator import ObjectiveWeights, evaluate, route_metrics
from vrp.model import (
    UNREACHABLE,
    Location,
    Order,
    Problem,
    Route,
    Solution,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DATASET = Path("data/deliveries_cr.json")

# A working day, in whole seconds from midnight.
SHIFT = TimeWindow(start=6 * 3600, end=18 * 3600)


def load_slice(path: Path, stops: int, province: str | None) -> tuple[list[dict], dict]:
    """Take a solvable slice of the dataset, nearest first to one depot.

    50,000 deliveries is a planning corpus, not one request: the gateway caps a
    solve at VRP_MAX_STOPS and a dense matrix is quadratic besides. Slicing by
    proximity to a single depot gives a realistic day's work rather than points
    scattered across the country.
    """
    data = json.loads(path.read_text())
    deliveries = data["deliveries"]
    if province:
        deliveries = [d for d in deliveries if d["province"] == province]
        if not deliveries:
            raise SystemExit(f"no deliveries in province {province!r}")

    # The depot with the most of this work nearby.
    depot = min(
        data["depots"],
        key=lambda w: sum((d["latitude"] - w["latitude"]) ** 2
                          + (d["longitude"] - w["longitude"]) ** 2
                          for d in deliveries[:400]),
    )
    nearest = sorted(deliveries,
                     key=lambda d: (d["latitude"] - depot["latitude"]) ** 2
                     + (d["longitude"] - depot["longitude"]) ** 2)
    return nearest[:stops], depot


def fetch_matrix(depot: dict, deliveries: list[dict]) -> tuple[list[list], list[list]]:
    """Real road durations and distances, from the gateway's /matrix."""
    coordinates = [{"longitude": depot["longitude"], "latitude": depot["latitude"]}]
    coordinates += [{"longitude": d["longitude"], "latitude": d["latitude"]}
                    for d in deliveries]
    response = httpx.post(f"{GATEWAY}/matrix",
                          json={"coordinates": coordinates,
                                "annotations": "duration,distance"},
                          timeout=120)
    if response.status_code != 200:
        raise SystemExit(f"gateway returned {response.status_code}: {response.text[:200]}")
    body = response.json()
    return body["durations"], body["distances"]


def to_problem(depot: dict, deliveries: list[dict],
               durations: list[list], distances: list[list]) -> Problem:
    """Turn the slice into a `Problem`, keeping `product_id` as the order id.

    That is what the field is for: the same identifier travels from the dataset
    through allocation and sequencing into whatever executes the plan, so a
    stop can be traced end to end without a lookup table.
    """
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for index, delivery in enumerate(deliveries, start=1):
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"],
                                  matrix_index=index))
        orders.append(Order(
            id=delivery["product_id"],
            kind="JOB",
            # Weight in whole grams: the model is integer-only, and rounding
            # kilograms to whole numbers would throw away most of the range.
            quantities={"grams": round(delivery["weight_kg"] * 1000)},
            delivery=StopSpec(location_id=delivery["product_id"],
                              time_windows=(SHIFT,),
                              service_fixed=delivery["service_minutes"] * 60),
        ))

    # OSRM returns nulls for unreachable pairs and floats throughout; the model
    # takes whole seconds and metres.
    def grid(raw: list[list]) -> tuple[tuple[int, ...], ...]:
        # MTX-5: a null cell is unreachable, and must stay distinguishable.
        # This said `10 ** 9` before E-10 -- a large finite arc a solver will
        # happily optimise into a plan, returning a leg nobody can drive.
        return tuple(tuple(round(cell) if cell is not None else UNREACHABLE
                           for cell in row) for row in raw)

    total_grams = sum(o.quantities["grams"] for o in orders)
    vehicle = Vehicle(id="VAN-1",
                      capacities={"grams": total_grams},   # one van, whole load
                      shift=SHIFT,
                      start_location_id="DEPOT", end_location_id="DEPOT")
    return Problem(id="cr-demo", locations=tuple(locations), orders=tuple(orders),
                   vehicles=(vehicle,),
                   matrix=TravelMatrix(version="gateway-matrix",
                                       durations=grid(durations),
                                       distances=grid(distances)))


def nearest_neighbour(problem: Problem) -> list[str]:
    """A plausible route, built greedily. Not a solver — see the module docstring."""
    matrix = problem.matrix
    remaining = {order.id for order in problem.orders}
    index_of = {location.id: location.matrix_index for location in problem.locations}
    sequence, position = [], index_of["DEPOT"]
    while remaining:
        nearest = min(remaining, key=lambda order_id: matrix.duration(position,
                                                                     index_of[order_id]))
        sequence.append(nearest)
        position = index_of[nearest]
        remaining.discard(nearest)
    return sequence


def report(problem: Problem, sequence: list[str]) -> Solution:
    """Evaluate the plan and print what the canonical evaluator computed."""
    weights = ObjectiveWeights(per_metre=1, per_second=0, per_vehicle=50_000)
    result = evaluate(problem, {"VAN-1": sequence}, weights=weights)
    timeline = result.timelines["VAN-1"]
    metrics = route_metrics(problem, timeline)

    print("\n--- canonical evaluation -------------------------------------")
    print(f"  stops              {len(sequence)}")
    print(f"  distance           {metrics['distance'] / 1000:>8.1f} km")
    print(f"  driving            {metrics['driving_seconds'] / 3600:>8.2f} h")
    print(f"  service            {metrics['service_seconds'] / 3600:>8.2f} h")
    print(f"  waiting            {metrics['waiting_seconds'] / 3600:>8.2f} h")
    print(f"  finishes           {timeline[-1].arrival / 3600:>8.2f} h "
          f"(shift ends {SHIFT.end / 3600:.0f}h)")
    print(f"  objective          {result.total:,}")

    return Solution(
        problem_id=problem.id,
        routes=(Route(vehicle_id="VAN-1", steps=timeline),),
        objective_breakdown={"distance": metrics["distance"],
                             "driving_seconds": metrics["driving_seconds"]},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", type=int, default=40)
    parser.add_argument("--province", default=None,
                        help="restrict to one province, e.g. 'San Jose'")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}\n"
              f"generate it first -- see docs/dataset_prep.md", file=sys.stderr)
        return 2

    deliveries, depot = load_slice(args.dataset, args.stops, args.province)
    print(f"gateway  {GATEWAY}")
    print(f"depot    {depot['name']}")
    print(f"stops    {len(deliveries)}"
          + (f" in {args.province}" if args.province else ""))
    print(f"tracking {deliveries[0]['product_id']} … {deliveries[-1]['product_id']}")

    durations, distances = fetch_matrix(depot, deliveries)
    problem = to_problem(depot, deliveries, durations, distances)
    sequence = nearest_neighbour(problem)
    solution = report(problem, sequence)

    # --- the plan is judged by something that did not build it ---------------
    print("\n--- independent verification ---------------------------------")
    verdict = verify(problem, solution)
    if verdict.ok:
        print("  PASS  every applicable invariant holds")
    else:
        print(f"  FAIL  {len(verdict.violations)} violation(s)")
        for violation in verdict.violations[:10]:
            print(f"        {violation}")
    for invariant in sorted(verdict.not_applicable):
        print(f"  n/a   {invariant}")

    # --- and the failure mode it exists to catch -----------------------------
    print("\n--- objective drift (INV-9) ----------------------------------")
    drifted = Solution(
        problem_id=solution.problem_id,
        routes=solution.routes,
        # One kilometre short: the kind of error an incremental evaluator makes
        # when a move's delta is computed slightly wrong, and which no amount of
        # staring at the plan reveals.
        objective_breakdown={**solution.objective_breakdown,
                             "distance": solution.objective_breakdown["distance"] - 1000},
    )
    caught = verify(problem, drifted)
    print(f"  reported distance understated by 1 km -> "
          f"{'CAUGHT' if not caught.ok else 'MISSED'}")
    for violation in caught.violations:
        print(f"        {violation}")

    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
