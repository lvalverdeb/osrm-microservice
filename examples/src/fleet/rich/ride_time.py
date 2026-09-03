"""How long it may be aboard is not the same as when it may be dropped.

Demonstrates the ride-time bound landed for E-74/T-74 (FR-24, INV-14, §12.2):

    vrp.model      `Order.max_ride_time`, on a shipment
    vrp.solve      the delivery deadline the bound implies, derived per order
    vrp.verify     INV-14, which measures the journey exactly

FR-24: "Support a **maximum ride time** between a shipment's pickup and its
delivery -- for passengers, time aboard; for goods, a viability or working-life
clock that starts at loading and is not the delivery window."

Seven operations asked for it, and two of them break on the same confusion in
almost the same words. `UC-092` (ready-mix concrete): "the clock starts at
loading, so the constraint is elapsed time since departure, not arrival time at
the customer." `UC-157` (blood and organ transport): "the clock starts at
collection, making it a maximum elapsed time per shipment, not an arrival
window." And `UC-026` (school buses) says what happens without one: "a
cost-optimal route can leave a five-year-old aboard for 90 minutes."

Three things, in order:

1. **Two constraints, not one.** A delivery window and a ride bound restrict
   different things, and a real order carries both: a sample due at the lab
   before its cut-off *and* viable for only six hours.

2. **What the search is told.** `add_shipment` takes no ride bound, so the only
   lever is a deadline -- and the sound one is the earliest the collection
   could physically happen plus the bound. Conservative, and exact when the
   collection time is fixed, which is what a scheduled stop is.

3. **What the verifier measures.** The journey itself, departure to arrival,
   with no approximation. That is the check `/verify` runs on a plan built
   somewhere else, which is the only place the exact rule can be applied.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/ride_time.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import delivery_deadline, solve
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)
MINUTE = 60


def instance(orders: tuple[Order, ...], stops: int = 4) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * MINUTE for j in range(size))
                 for i in range(size))
    return Problem(
        id="ride", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="ride", durations=grid, distances=grid))


def shipment(order_id: str, collect: str, drop: str, *,
             ride: int | None = None, deliver_by: TimeWindow = DAY) -> Order:
    return Order(
        id=order_id, kind="SHIPMENT", quantities={"kg": 1}, max_ride_time=ride,
        pickup=StopSpec(location_id=collect, time_windows=(DAY,),
                        service_fixed=MINUTE),
        delivery=StopSpec(location_id=drop, time_windows=(deliver_by,),
                          service_fixed=MINUTE))


def job(order_id: str, stop: str) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": 1},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=MINUTE))


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def two_constraints_not_one() -> None:
    heading("1.", "Two constraints, not one")
    cutoff = TimeWindow(start=0, end=5 * 3600)
    sample = shipment("SAMPLE", "C1", "C2", ride=6 * 3600, deliver_by=cutoff)
    print(f"\n   the lab shuts at   {cutoff.end // 3600:02d}:00   "
          "(a delivery window: when the drop may happen)")
    print(f"   the sample lasts   {sample.max_ride_time // 3600:02d}:00   "
          "(a ride bound: how long the journey may take)")
    print("\n   Collect it at 04:00 and deliver at 04:30 and both hold. Collect")
    print("   it at 22:00 the night before and deliver at 04:30 and the window")
    print("   still holds while the sample is six and a half hours old. Reading")
    print("   one as the other passes plans in which it arrives on time and")
    print("   useless.")


def what_the_search_is_told() -> None:
    heading("2.", "What the search is told")
    bound = 4 * MINUTE
    problem = instance((shipment("URGENT", "C1", "C4", ride=bound),
                        job("ONWAY1", "C2"), job("ONWAY2", "C3")))
    order = problem.order("URGENT")
    deadline = delivery_deadline(problem, order)
    print(f"\n   bound {bound // 60} min; the van is a minute from the pickup "
          f"and a minute of service")
    print(f"   derived delivery deadline: {deadline}s "
          f"(= earliest possible departure + bound)")
    print("\n   The two fillers sit between the pickup and the delivery, so")
    print("   collecting them en route costs no extra distance and the search")
    print("   would happily do it. The bound is the only thing that stops it:")

    plan = solve(problem, iterations=400, seed=0)
    aboard = {}
    for step in plan.routes[0].steps:
        if step.order_id == "URGENT":
            aboard[step.type] = step.departure if step.type == "PICKUP" else step.arrival
        print(f"      {step.type:9s} {step.order_id or '-':7s} "
              f"arrive {step.arrival:5d}  depart {step.departure:5d}")
    print(f"\n   URGENT was aboard {aboard['DELIVERY'] - aboard['PICKUP']}s of "
          f"an allowed {bound}s, and the plan verifies: "
          f"{verify(problem, plan).ok}")


def what_the_verifier_measures() -> None:
    heading("3.", "What the verifier measures")
    from vrp.model import Route, Solution, Step

    problem = instance((shipment("RIDE", "C1", "C2", ride=MINUTE),
                        job("DETOUR", "C4")))
    # Every leg exactly what the matrix says, so nothing else is wrong with it.
    plan = Solution(problem_id=problem.id, status="FEASIBLE", routes=(Route(
        vehicle_id="V1", steps=(
            Step(type="START", location_id="D", arrival=0, start_service=0,
                 departure=0),
            Step(type="PICKUP", order_id="RIDE", location_id="C1", arrival=60,
                 start_service=60, departure=120),
            Step(type="DELIVERY", order_id="DETOUR", location_id="C4",
                 arrival=300, start_service=300, departure=360),
            Step(type="DELIVERY", order_id="RIDE", location_id="C2",
                 arrival=480, start_service=480, departure=540),
            Step(type="END", location_id="D", arrival=660, start_service=660,
                 departure=660))),))
    report = verify(problem, plan)
    print("\n   a plan built elsewhere, every leg matching the matrix:")
    print("      collected at 120, delivered at 480, bound 60")
    for violation in report.violations:
        print(f"      -> {violation.invariant}: {violation.detail}")
    print("\n   This is the exact rule, and the only place it can be applied is")
    print("   a finished plan. `/verify` is public for that reason (CON-1), and")
    print("   the deadline in §2 is the search's safe approximation of it.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-24, from CAT-VRP-003 §12.2 -- seven operations asked for this.")
    two_constraints_not_one()
    what_the_search_is_told()
    what_the_verifier_measures()
    print(f"\n{'=' * 72}")
    print("A delivery window says when the drop may happen. A ride bound says")
    print("how long the journey may take. An operation can need both, and until")
    print("FR-24 the model could only say one of them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
