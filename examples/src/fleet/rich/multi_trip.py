"""A second trip the driver has to pay for, and eight bays for forty vans.

Demonstrates multi-trip, reloading and dock capacity, landed for E-28/T-28
(FR-09, FR-19, §6.8, §6.9):

    vrp.verify         INV-11 (reloads) and INV-12 (dock occupancy)
    vrp.hos.schedule   the duty the second trip comes out of
    vrp.model          `reload_locations`, `max_reloads`, `dock_capacity`

§6.8 sets out the requirement and, unusually, also the wrong way to meet it:
"A vehicle that empties before its shift ends should return, reload, and go
again... Combined with driving-hours rules this becomes a multi-trip VRPTW with
an embedded driver scheduling problem; it MUST NOT be approximated by chaining
independent single-trip plans, which double-counts driver availability."

That prohibition is the interesting part. Two single-trip plans for one van look
perfectly reasonable side by side and are jointly impossible: each assumes a
full duty, so the driver works sixteen hours. The question worth asking is not
"can a van do two trips" but "does the second trip cost the driver anything".

Four things this shows, in order:

1. **Capacity across a day rather than across a trip.** A 100 kg van delivering
   120 kg, because it went back for the rest.
2. **What the second trip costs.** The same two trips under EU-561, where they
   do not fit one duty. This is §6.8's prohibition, made concrete.
3. **Where a van may reload, and how often.** Not on a customer's doorstep, and
   not more times than the vehicle allows.
4. **§6.9's loading bays.** Three vans, one bay, all departing at once.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/multi_trip.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.hos import EU_561
from vrp.hos.schedule import schedule_route
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(orders, vehicles, stops: int, leg: int = 600,
             depot: Location | None = None) -> Problem:
    size = stops + 1
    depot = depot or Location(id="D", lat=9.9, lon=-84.0, matrix_index=0)
    locations = (depot, *(
        Location(id=f"C{i}", lat=9.9 + i / 1000, lon=-84.0, matrix_index=i)
        for i in range(1, size)))
    grid = tuple(tuple(abs(i - j) * leg for j in range(size))
                 for i in range(size))
    return Problem(id="mt", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="mt", durations=grid,
                                       distances=grid))


def an_order(order_id: str, stop: str, kg: int) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": kg},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=60))


def a_van(**kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=kwargs.pop("id", "V1"), **{**defaults, **kwargs})


def report_on(problem: Problem, plan: Solution, label: str) -> None:
    result = verify(problem, plan)
    if result.ok:
        print(f"   {label:<40} accepted")
        return
    for violation in result.violations:
        print(f"   {label:<40} {violation.invariant}: {violation.detail}")


def two_trip_plan(problem: Problem, *, reload_at: str = "D",
                  reloads: int = 1) -> Solution:
    """One duty, out and back and out again, timed off the matrix.

    Every arrival is derived rather than written down, so INV-4 has nothing to
    say and the only thing left to fail is the reload rule under test.
    """
    index = {location.id: location.matrix_index
             for location in problem.locations}
    itinerary = [("DELIVERY", "C1", "O1"),
                 *[("RELOAD", reload_at, None)] * reloads,
                 ("DELIVERY", "C2", "O2")]

    steps = [Step(type="START", location_id="D", arrival=0, start_service=0,
                  departure=0, load_after={"kg": 60})]
    clock, here = 0, index["D"]
    for kind, location_id, order_id in itinerary:
        there = index[location_id]
        clock += problem.matrix.duration(here, there)
        duration = 900 if kind == "RELOAD" else 60
        steps.append(Step(type=kind, location_id=location_id,
                          order_id=order_id, arrival=clock, start_service=clock,
                          departure=clock + duration,
                          load_after={"kg": 60 if kind == "RELOAD" else 0}))
        clock, here = clock + duration, there
    clock += problem.matrix.duration(here, index["D"])
    steps.append(Step(type="END", location_id="D", arrival=clock,
                      start_service=clock, departure=clock,
                      load_after={"kg": 0}))
    return Solution(problem_id=problem.id, unassigned=(),
                    objective_breakdown={}, status="FEASIBLE",
                    routes=(Route(vehicle_id="V1", steps=tuple(steps)),))


def show_the_reload() -> None:
    """§6.8: a reload "resets load to zero (or to a newly loaded state)"."""
    print("\n1. 120 kg through a 100 kg van")
    orders = (an_order("O1", "C1", kg=60), an_order("O2", "C2", kg=60))
    van = a_van(reload_locations=frozenset({"D"}), max_reloads=1,
                reload_duration=900)
    problem = instance(orders, (van,), stops=2)

    report_on(problem, two_trip_plan(problem), "two trips, reloading at D")
    print("   A verifier carrying the load across the reload would reject every")
    print("   legal multi-trip plan; one ignoring the step would accept any")
    print("   overload. The RELOAD step has to mean something specific.")


def show_the_cost() -> None:
    """The prohibition: a second trip is not free."""
    print("\n2. What the second trip costs the driver")
    orders = tuple(an_order(f"O{i}", f"C{i}", kg=60) for i in (1, 2))
    van = a_van(reload_locations=frozenset({"D"}), max_reloads=1,
                reload_duration=3600, hos_rules="EU-561")
    problem = instance(orders, (van,), stops=2, leg=4 * 3600)

    one = schedule_route(problem, "V1", ["O1"], EU_561)
    both = schedule_route(problem, "V1", ["O1", "O2"], EU_561)
    print("   4-hour legs, EU-561's 9-hour driving limit")
    print(f"   one trip  (8h driving): legal={one.legal}")
    print(f"   two trips (16h driving): legal={both.legal}")
    print("   Planned separately, each trip is a perfectly good day's work and")
    print("   the pair is impossible. That is exactly the approximation §6.8")
    print("   forbids: the driver's availability gets counted twice.")


def show_where_and_how_often() -> None:
    """INV-11, both halves."""
    print("\n3. Where a van may reload, and how often")
    orders = (an_order("O1", "C1", kg=60), an_order("O2", "C2", kg=60))

    permitted = instance(orders, (a_van(reload_locations=frozenset({"D"}),
                                        max_reloads=1, reload_duration=900),),
                         stops=2)
    report_on(permitted, two_trip_plan(permitted, reload_at="C1"),
              "reloading at a customer's doorstep")

    twice = instance(orders, (a_van(reload_locations=frozenset({"D"}),
                                    max_reloads=1, reload_duration=900),),
                     stops=2)
    report_on(twice, two_trip_plan(twice, reloads=2),
              "two reloads where one is permitted")
    print("   `reload_locations` names where stock actually is. A plan that")
    print("   reloads elsewhere is describing a depot that does not exist.")


def show_dock_capacity() -> None:
    """§6.9: "If 40 vehicles are planned to depart at 06:00 and there are 8
    bays, the plan is fiction"."""
    print("\n4. Loading bays")
    orders = tuple(an_order(f"O{i}", f"C{i}", kg=10) for i in (1, 2, 3))
    fleet = tuple(a_van(id=f"V{n}") for n in (1, 2, 3))

    for bays in (1, 3, None):
        depot = Location(id="D", lat=9.9, lon=-84.0, matrix_index=0,
                         dock_capacity=bays)
        problem = instance(orders, fleet, stops=3, depot=depot)
        routes = tuple(
            Route(vehicle_id=f"V{n}", steps=(
                Step(type="START", location_id="D", arrival=0, start_service=0,
                     departure=1800, load_after={"kg": 10}),
                Step(type="DELIVERY", location_id=order.delivery.location_id,
                     order_id=order.id, arrival=1800 + 600 * n,
                     start_service=1800 + 600 * n, departure=1860 + 600 * n,
                     load_after={"kg": 0}),
                Step(type="END", location_id="D", arrival=1860 + 1200 * n,
                     start_service=1860 + 1200 * n, departure=1860 + 1200 * n,
                     load_after={"kg": 0}),
            ))
            for n, order in enumerate(orders, start=1))
        plan = Solution(problem_id=problem.id, routes=routes, unassigned=(),
                        objective_breakdown={}, status="FEASIBLE")
        label = "unlimited" if bays is None else f"{bays} bay(s)"
        report_on(problem, plan, f"three vans, one 30-min load, {label}")

    print("   All three vans occupy the depot for the same half hour, which is")
    print("   what the START span is for. Unset means unlimited rather than")
    print("   zero: most depots are not the constraint, and the inverse reading")
    print("   would make every plan fiction the moment one depot went unmeasured.")
    print("   FR-19 is a SHOULD, so this is checked and reported, not enforced")
    print("   inside the search.")


def main() -> int:
    show_the_reload()
    show_the_cost()
    show_where_and_how_often()
    show_dock_capacity()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
