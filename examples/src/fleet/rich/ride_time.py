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
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.model import (
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
    service_time,
)
from vrp.solve.pyvrp_adapter import delivery_deadline, solve
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)
MINUTE = 60


# Four real deliveries around the Guadalupe depot. C1 and C4 are both
# pharmacies, which is where the sample story comes from: the corpus chose
# them, not the example.
LOCATIONS, MATRIX, DELIVERIES, DEPOT = dataset.planar_sites(4, "spread", "ride")


def service_at(stop: str) -> int:
    """The corpus's own service time at one of the sites."""
    return DELIVERIES[int(stop[1:]) - 1]["service_minutes"] * MINUTE


def instance(orders: tuple[Order, ...], stops: int = 4) -> Problem:
    """The real round, carrying whatever orders a section needs.

    Args:
        orders: The shipments and jobs to place.
        stops: How many of the four sites to expose.

    Returns:
        The instance, over real addresses and real service times.
    """
    return Problem(
        id="ride", locations=LOCATIONS[:stops + 1], orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=MATRIX)


def shipment(order_id: str, collect: str, drop: str, *,
             ride: int | None = None, deliver_by: TimeWindow = DAY) -> Order:
    return Order(
        id=order_id, kind="SHIPMENT", quantities={"kg": 1}, max_ride_time=ride,
        pickup=StopSpec(location_id=collect, time_windows=(DAY,),
                        service_fixed=service_at(collect)),
        delivery=StopSpec(location_id=drop, time_windows=(deliver_by,),
                          service_fixed=service_at(drop)))


def job(order_id: str, stop: str) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": 1},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=service_at(stop)))


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


def aboard_for(bound: int) -> tuple[int, int, bool]:
    """Solve with one ride bound and report what the plan does with it.

    Args:
        bound: The maximum ride time, in seconds.

    Returns:
        `(seconds aboard, derived deadline, whether the verifier accepts)`.
    """
    problem = instance((shipment("URGENT", "C1", "C4", ride=bound),
                        job("ONWAY1", "C2"), job("ONWAY2", "C3")))
    plan = solve(problem, iterations=400, seed=0)
    marks = {}
    for step in plan.routes[0].steps:
        if step.order_id == "URGENT":
            marks[step.type] = (step.departure if step.type == "PICKUP"
                                else step.arrival)
    deadline = delivery_deadline(problem, problem.order("URGENT"))
    return (marks["DELIVERY"] - marks["PICKUP"], deadline,
            verify(problem, plan).ok)


def what_the_search_is_told() -> None:
    heading("2.", "What the search is told, and when it makes any difference")
    direct = MATRIX.duration(1, 4)
    detour = (MATRIX.duration(1, 2) + service_at("C2")
              + MATRIX.duration(2, 3) + service_at("C3")
              + MATRIX.duration(3, 4))
    print(f"\n   C1 to C4 direct is {direct // 60} min. Going via C2 and C3 --")
    print(f"   which costs the round no extra distance -- is {detour // 60} min")
    print("   once their service times are paid. `add_shipment` takes no ride")
    print("   bound, so the only lever is a delivery deadline: the earliest")
    print("   the collection could physically happen, plus the bound.\n")
    print(f"      {'bound':>7s} {'deadline':>9s} {'aboard':>7s}   what the plan does")
    for minutes in (10, 20, 30, 40, 50):
        seconds, deadline, ok = aboard_for(minutes * MINUTE)
        detoured = seconds > direct
        print(f"      {minutes:5d} m {deadline:8d}s {seconds // 60:5d} m   "
              f"{'collects both en route' if detoured else 'goes straight there'}"
              f"{'' if ok else '  (verifier REJECTS)'}")
    print("\n   The bound only decides anything between the two numbers above.")
    print("   Tighter and there is nothing to give up; looser and the detour")
    print("   was always allowed. That is why the operations that asked for")
    print("   this constraint -- concrete at 90 minutes, blood at a few hours --")
    print("   are the ones whose clock is commensurate with the round. A")
    print("   six-hour viability bound on a 35-minute round is a comment.")


def _walk(problem: Problem, legs: tuple) -> tuple:
    """A timeline whose every leg is exactly what the matrix says.

    Building it by hand is the point: this is a plan that arrived from
    somewhere else, and the only thing wrong with it must be the ride bound.
    Hard-coding the clock would make INV-3 and INV-4 fire too and bury the one
    violation the section is about.

    Args:
        problem: The instance, for the matrix and the service times.
        legs: `(step type, location id, order id)` in order, depot excluded.

    Returns:
        The steps, depot to depot.
    """
    from vrp.model import Step

    index = {location.id: location.matrix_index
             for location in problem.locations}
    steps = [Step(type="START", location_id="D", arrival=0, start_service=0,
                  departure=0)]
    clock, here = 0, index["D"]
    vehicle = problem.vehicles[0]
    for kind, site, order_id in legs:
        clock += problem.matrix.duration(here, index[site])
        # `service_time` rather than the corpus figure directly: it is the
        # public form of the rule the verifier applies, and duplicating that
        # rule here is how a plan ends up disagreeing with INV-3 about its own
        # arithmetic. See the module docstring on shipment pickups.
        service = service_time(problem.order(order_id), vehicle,
                               problem.location(site))
        steps.append(Step(type=kind, order_id=order_id, location_id=site,
                          arrival=clock, start_service=clock,
                          departure=clock + service))
        clock, here = clock + service, index[site]
    clock += problem.matrix.duration(here, index["D"])
    steps.append(Step(type="END", location_id="D", arrival=clock,
                      start_service=clock, departure=clock))
    return tuple(steps)


def what_the_verifier_measures() -> None:
    heading("3.", "What the verifier measures")
    from vrp.model import Route, Solution

    problem = instance((shipment("RIDE", "C1", "C2", ride=MINUTE),
                        job("DETOUR", "C4")))
    steps = _walk(problem, (("PICKUP", "C1", "RIDE"),
                            ("DELIVERY", "C4", "DETOUR"),
                            ("DELIVERY", "C2", "RIDE")))
    plan = Solution(problem_id=problem.id, status="FEASIBLE",
                    routes=(Route(vehicle_id="V1", steps=steps),))
    aboard = {step.type: step for step in steps if step.order_id == "RIDE"}
    report = verify(problem, plan)
    print("\n   a plan built elsewhere, every leg exactly what the matrix says:")
    print(f"      collected at {aboard['PICKUP'].departure}, delivered at "
          f"{aboard['DELIVERY'].arrival}, bound {MINUTE}")
    for violation in report.violations:
        print(f"      -> {violation.invariant}: {violation.detail}")
    print("\n   One violation and only one. The detour to C4 is a perfectly")
    print("   good piece of routing and every other invariant agrees; the")
    print("   sample was simply aboard too long while it happened.")
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
