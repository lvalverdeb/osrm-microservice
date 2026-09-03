"""Pricing a stop while the customer is still on the phone.

Demonstrates quotes landed for E-85/T-85 (NFR-02, §9.4):

    vrp.quote.quote_insertion   what adding this stop would cost
    vrp.quote.quote_removal     what dropping that one would save

`NFR-02`: "Single-order insertion / removal quote: p95 ≤ 2 s." §9.4 gives it an
endpoint: `POST /v1/solutions/{id}/quote`.

The point of a quote is what it does *not* do. A dispatcher asking "can we fit
this in today, and what does it cost?" is not asking for the round to be
replanned — every other stop has a promised time and half the vans have left.

Four things, in order:

1. **A price, and nothing else moved.** The candidate goes into one position on
   one route; every other route is exactly where it was.

2. **What "price" means.** The canonical objective carries a penalty for orders
   nobody serves, and it dwarfs the running cost. Quote the raw delta and every
   insertion appears to *save* money.

3. **A refusal, when there is no room.** Not a very large number.

4. **The latency, measured** — NFR-02's clause, on a real day's work: four
   hundred Costa Rica deliveries across forty vans.

Runs offline. No gateway required: the matrix is planar over real
coordinates.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/dynamic/insertion_quote.py
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.evaluator import ObjectiveWeights, evaluate
from vrp.matrix import PlanarMatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.quote import NoRoomForOrder, quote_insertion, quote_removal

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)


def a_round(stops: int = 12, vans: int = 4, capacity: int = 100,
            leg: int = 600) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * leg for j in range(size))
                 for i in range(size))
    return Problem(
        id="quote",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": capacity},
                               shift=DAY, start_location_id="D",
                               end_location_id="D", cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=TravelMatrix(version="q", durations=grid, distances=grid))


def a_real_day(stops: int, vans: int) -> Problem:
    """A day of real deliveries around one depot, priced planar.

    Real geography rather than stops on a line. The synthetic version of this
    needed its legs shortened to four seconds before a four-hundredth stop was
    reachable inside a shift at all -- a fudge that says more about the
    generator than about quoting, and one a real catchment does not need.
    """
    corpus = dataset.load(dataset.DEFAULT_PATH)
    deliveries, depot = corpus.nearest(stops)

    lat_km = 110.57
    lon_km = 111.32 * math.cos(math.radians(depot["latitude"]))
    coords = [(0.0, 0.0)] + [
        ((d["longitude"] - depot["longitude"]) * lon_km,
         (d["latitude"] - depot["latitude"]) * lat_km) for d in deliveries]

    heaviest = max((d["units"] for d in deliveries), default=1)
    return Problem(
        id=f"real-{stops}",
        locations=(Location(id="D", lat=depot["latitude"],
                            lon=depot["longitude"], matrix_index=0),) + tuple(
            Location(id=d["product_id"], lat=d["latitude"], lon=d["longitude"],
                     matrix_index=i + 1)
            for i, d in enumerate(deliveries)),
        orders=tuple(
            Order(id=f"O{i + 1}", kind="JOB", quantities={"units": d["units"]},
                  delivery=StopSpec(location_id=d["product_id"],
                                    time_windows=(DAY,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=tuple(
            Vehicle(id=f"V{n}", capacities={"units": heaviest * 30}, shift=DAY,
                    start_location_id="D", end_location_id="D",
                    cost_per_metre=1)
            for n in range(1, vans + 1)),
        matrix=PlanarMatrix(version="real-v1", coordinates=tuple(coords)))


def spread(problem: Problem, vans: int, reserve: int = 1) -> dict:
    served = [order.id for order in problem.orders][:-reserve]
    assignment = {f"V{n}": [] for n in range(1, vans + 1)}
    for position, order_id in enumerate(served):
        assignment[f"V{position % vans + 1}"].append(order_id)
    return assignment


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def a_price_and_nothing_else_moved() -> None:
    heading("1.", "The price, and the plan it does not disturb")
    problem = a_round()
    before = spread(problem, vans=4)
    candidate = problem.orders[-1].id

    quote = quote_insertion(problem, before, candidate)
    print(f"\n   adding {candidate}: {quote.price:,} on {quote.vehicle_id}\n")
    print(f"      {'van':5s} {'before':32s} after")
    for vehicle_id in sorted(before):
        after = (list(quote.route) if vehicle_id == quote.vehicle_id
                 else before[vehicle_id])
        marker = "  <-" if vehicle_id == quote.vehicle_id else ""
        print(f"      {vehicle_id:5s} {before[vehicle_id]!s:32s} "
              f"{after}{marker}")
    print("\n   One route gains one stop in one position. Nothing else moved,")
    print("   which is what makes it a quote rather than a replan.")


def what_price_means() -> None:
    heading("2.", "Why the raw objective is the wrong number")
    problem = a_round()
    before = spread(problem, vans=4)
    candidate = problem.orders[-1].id
    quote = quote_insertion(problem, before, candidate)

    after = {v: list(o) for v, o in before.items()}
    after[quote.vehicle_id] = list(quote.route)
    raw_before = evaluate(problem, before, ObjectiveWeights())
    raw_after = evaluate(problem, after, ObjectiveWeights())

    print(f"\n      {'':22s} {'before':>12s} {'after':>12s} {'delta':>12s}")
    for label, key in (("whole objective", None),
                       ("unassigned penalty", "unassigned_penalty")):
        b = raw_before.total if key is None else raw_before.breakdown.get(key, 0)
        a = raw_after.total if key is None else raw_after.breakdown.get(key, 0)
        print(f"      {label:22s} {b:12,d} {a:12,d} {a - b:12,d}")
    print(f"      {'cost of serving':22s} "
          f"{raw_before.total - raw_before.breakdown.get('unassigned_penalty', 0):12,d} "
          f"{raw_after.total - raw_after.breakdown.get('unassigned_penalty', 0):12,d} "
          f"{quote.price:12,d}")
    print("\n   The whole objective says adding a stop saves the better part of")
    print("   a million, because it stops paying the penalty for not serving")
    print("   it. That penalty is how a solver is told to prefer serving")
    print("   things; it is not a price, and quoting it would tell a customer")
    print("   their delivery pays for itself.")


def no_room() -> None:
    heading("3.", "When the answer is no")
    problem = a_round(stops=6, vans=1, capacity=3)
    try:
        quote_insertion(problem, {"V1": ["O1", "O2", "O3"]}, "O6")
    except NoRoomForOrder as refusal:
        text = str(refusal)
    print(f"\n      {text[:66]}\n      {text[66:136]}\n      {text[136:]}")
    print("\n   A dispatcher handed a very large number takes it to the")
    print("   customer. One told the van is full hires another van.")


def and_a_saving() -> None:
    heading("4.", "The other direction, and the clock")
    problem = a_round()
    before = spread(problem, vans=4)
    victim = before["V1"][-1]
    saving = quote_removal(problem, before, victim)
    print(f"\n   dropping {victim} from {saving.vehicle_id}: "
          f"{saving.price:,} ({saving.route})")

    big = a_real_day(stops=400, vans=40)
    plan = spread(big, vans=40)
    timings = []
    for _ in range(20):
        started = time.monotonic()
        quote_insertion(big, plan, big.orders[-1].id)
        timings.append(time.monotonic() - started)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    print(f"\n   {len(big.orders)} real deliveries across "
          f"{len(big.vehicles)} vans, 20 quotes:")
    print(f"      p95 {p95 * 1000:.0f} ms   worst {max(timings) * 1000:.0f} ms"
          f"   budget {2000} ms")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-02's first clause. T-56 already meets the second.")
    a_price_and_nothing_else_moved()
    what_price_means()
    no_room()
    and_a_saving()
    print(f"\n{'=' * 72}")
    print("A quote answers what this stop costs. Not what the day would look")
    print("like if you let the planner start again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
