"""A van is full when *any* dimension runs out — and totals are the wrong test.

Demonstrates the capacity work landed for E-20/T-20 against the Costa Rica
dataset and real road distances:

    vrp.model      multi-dimensional signed quantities
    vrp.osrm       a pinned matrix from the gateway
    vrp.solve      PyVRP, with deliveries and pickups told apart
    vrp.verify     INV-5, checked at every step rather than on the totals

§6.1: "A van is full when *any* of weight, volume, pallet positions, cage count,
or temperature-compartment volume is exhausted." And, in the same section, the
bug this example exists to make visible: "For simultaneous pickup-and-delivery,
the binding quantity is the **peak load along the route**, not the total —
computing feasibility from route totals is wrong and is a classic production
bug."

Three rounds over the same stops:

1. **Weight only.** One dimension, deliveries only. The load falls from full to
   empty and never rises, so peak and total are the same number — which is why
   a totals-based check survives in production for years before anything odd
   happens.

2. **Weight and volume.** The same round with cube added. Light bulky freight
   fills the van by volume long before it troubles the axle, and the plan
   changes even though not a gram was added.

3. **Deliveries and collections.** Half the stops now hand goods back. The load
   stops being monotonic: it falls at a drop and rises at a collection, and the
   binding number becomes the highest point along the way. The reported net is
   printed beside the peak so the gap between them is on the page.

Round 3 is the one to watch. Its net can sit comfortably inside the van while
the peak does not, and a planner comparing the net against capacity would sign
off a load nobody can physically stack.

Requires a running gateway; `examples/.env` points at the FreeBSD jail.
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/multi_capacity.py --stops 150
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

from vrp.matrix import build_large_matrix
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DATASET = Path("data/deliveries_cr.json")
SHIFT = TimeWindow(start=6 * 3600, end=20 * 3600)

# A 3.5-tonne van: about 1,200 kg of payload in roughly 10 m3 of hold. The two
# limits bind on different freight, which is the whole point of FR-02.
VAN_KG = 1_200
VAN_M3 = 10


def load(path: Path, stops: int) -> tuple[list[dict], dict]:
    data = json.loads(path.read_text())
    depot = data["depots"][0]
    nearest = sorted(data["deliveries"],
                     key=lambda d: (d["latitude"] - depot["latitude"]) ** 2
                     + (d["longitude"] - depot["longitude"]) ** 2)
    return nearest[:stops], depot


def cube_of(delivery: dict) -> int:
    """Volume in whole tenths of a cubic metre.

    The dataset carries weight but not volume, so this derives one: a category
    the dataset already marks as bulky gets a poor density, everything else a
    dense one. Invented, and said so -- the point is that two dimensions bind
    differently, which needs them to disagree, not to be accurate.
    """
    bulky = delivery.get("category", "") in ("FURNITURE", "APPLIANCE", "GARDEN")
    kilos = max(1, round(delivery["weight_kg"]))
    return max(1, round(kilos / (8 if bulky else 60) * 10))


def build(depot: dict, deliveries: list[dict], matrix: TravelMatrix,
          *, use_volume: bool, collections: bool) -> Problem:
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for offset, delivery in enumerate(deliveries):
        index = offset + 1
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"], matrix_index=index))
        quantities = {"kg": max(1, round(delivery["weight_kg"]))}
        if use_volume:
            quantities["dm3"] = cube_of(delivery)

        stop = StopSpec(location_id=delivery["product_id"],
                        time_windows=(SHIFT,),
                        service_fixed=delivery["service_minutes"] * 60)
        # Every other stop hands goods back rather than receiving them.
        collecting = collections and offset % 2 == 1
        orders.append(Order(
            id=delivery["product_id"], kind="JOB", quantities=quantities,
            pickup=stop if collecting else None,
            delivery=None if collecting else stop))

    capacities = {"kg": VAN_KG}
    if use_volume:
        capacities["dm3"] = VAN_M3 * 10
    fleet = tuple(
        Vehicle(id=f"VAN-{n}", capacities=capacities, shift=SHIFT,
                start_location_id="DEPOT", end_location_id="DEPOT")
        for n in range(1, 5)
    )
    return Problem(id="cap", locations=tuple(locations), orders=tuple(orders),
                   vehicles=fleet, matrix=matrix)


def profile(problem: Problem, solution, dimension: str) -> tuple[int, int]:
    """The peak load along every route, and the net the totals would report."""
    peak = 0
    for route in solution.routes:
        for step in route.steps:
            peak = max(peak, step.load_after.get(dimension, 0))
    delivered = sum(o.quantities.get(dimension, 0)
                    for o in problem.orders if o.delivery is not None)
    collected = sum(o.quantities.get(dimension, 0)
                    for o in problem.orders if o.pickup is not None)
    return peak, delivered - collected


def report(label: str, problem: Problem, solution) -> None:
    verdict = verify(problem, solution)
    used = [r for r in solution.routes if any(s.order_id for s in r.steps)]
    print(f"\n  {label}")
    print(f"    status        {solution.status}, verifier "
          f"{'accepts' if verdict.ok else 'REJECTS'}")
    if not verdict.ok:
        for violation in verdict.violations[:2]:
            print(f"      {violation}")
    print(f"    vans          {len(used)} of {len(problem.vehicles)}"
          + (f"   ({len(solution.unassigned)} stops unserved)"
             if solution.unassigned else ""))

    for dimension, limit, unit in (("kg", VAN_KG, "kg"),
                                   ("dm3", VAN_M3 * 10, "dm3")):
        if not any(dimension in o.quantities for o in problem.orders):
            continue
        peak, net = profile(problem, solution, dimension)
        flag = "  <-- over the van" if peak > limit else ""
        print(f"    peak {unit:<5}    {peak:>6} of {limit}"
              f"   (totals would say {net}){flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # A 3.5-tonne van holds about 140 of this dataset's parcels, and
    # capacity that never binds demonstrates nothing about capacity.
    parser.add_argument("--stops", type=int, default=150)
    parser.add_argument("--iterations", type=int, default=1200)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()

    if not args.dataset.exists():
        raise SystemExit(f"no dataset at {args.dataset}; see docs/dataset_prep.md")

    deliveries, depot = load(args.dataset, args.stops)
    print(f"depot {depot['name']} -- {len(deliveries)} stops, "
          f"van {VAN_KG} kg / {VAN_M3} m3")
    print(f"fetching a road matrix from {GATEWAY}")
    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    # Enough stops to actually fill a van means more than 10,000 matrix cells,
    # which the gateway refuses outright -- so this goes through E-11's tiling
    # rather than a single call.
    matrix, _ = build_large_matrix(GATEWAY, points)

    print(f"\nsolving three capacity models ({args.iterations} iterations each)")
    for label, kwargs in (
        ("weight only, deliveries only", {"use_volume": False, "collections": False}),
        ("weight and volume", {"use_volume": True, "collections": False}),
        ("weight and volume, half the stops collecting",
         {"use_volume": True, "collections": True}),
    ):
        problem = build(depot, deliveries, matrix, **kwargs)
        report(label, problem, solve(problem, iterations=args.iterations, seed=0))

    print("\n" + "=" * 72)
    print("Adding volume changes the plan without adding a gram: the van fills")
    print("by cube first. Adding collections breaks the load's monotonicity, and")
    print("the peak parts company with the net -- which is the number a")
    print("totals-based capacity check would have compared against the van.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
