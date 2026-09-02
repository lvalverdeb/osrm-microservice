"""A van that has to stop for electricity, and a plan that knows when.

Demonstrates EV range and recharging landed for E-41/T-41 (FR-20, INV-16):

    vrp.battery    consumption, and the charging curve
    vrp.electric   where a round has to stop, or why it cannot be driven

`FR-20` is the backlog's only `COULD` and was its only task with no data
source: nobody here has charger locations or a manufacturer's charging curve.
Its definition of done asks for range never violated on a **generated** EV
corpus, and generating one needs neither -- which is why it was buildable.

Five things, in order:

1. **The curve, which is the requirement.** A battery that charged at a
   constant rate would make the charging time a division. Real cells taper
   near the top, which is why a driver charges to eighty percent and drives on.

2. **A round beyond the battery.** Four stops, thirty kilometres apart, and a
   van that runs out on the way home. The state of charge is in the timeline,
   so the shortfall is visible at the step where it happens.

3. **The stop, placed.** Late rather than early -- an emptier battery takes
   current faster -- and charged to what the rest of the round needs rather
   than to full, because the last fifth is the expensive fifth.

4. **Why it has to be a step.** The charge pushes every later arrival by its
   own duration. That is the difference between modelling the constraint and
   accounting for it: an hour in a report cannot break a time window.

5. **The round nobody can drive.** A smaller battery makes the work
   impossible rather than the plan wrong, and it is refused by name. A
   dispatcher can hire a diesel van in ten minutes and cannot un-strand a
   driver.

Runs offline. The chargers and the curve are invented, and the corpus is
generated -- which is the point rather than an apology.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/ev_recharging.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.battery import FULL_PPT, ChargingCurve, charge_seconds
from vrp.electric import RESERVE_PPT, NoChargerReachable, plan_charging
from vrp.evaluator import build_timeline
from vrp.model import (
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

HOUR = 3600
KM = 1000
ORDERS = ["O1", "O2", "O3", "O4"]


def curve() -> ChargingCurve:
    """60 kW to eighty percent, 20 kW after. The shape every EV has."""
    return ChargingCurve(bands=((800, 60_000), (FULL_PPT, 20_000)))


def a_round(battery_wh: int, hop_km: int = 30) -> Problem:
    """Four stops out along a road, a charger beside the second."""
    day = TimeWindow(start=0, end=20 * HOUR)
    ids = ["D", "C1", "C2", "C3", "C4", "CH"]
    positions = [0, 1, 2, 3, 4, 2]
    locations = tuple(
        Location(id=site, lat=9.9 + index / 100, lon=-84.0, matrix_index=index)
        for index, site in enumerate(ids))
    metres = tuple(tuple(abs(a - b) * hop_km * KM for b in positions)
                   for a in positions)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=300,
                                time_windows=(day,)))
        for i in range(1, 5))
    return Problem(
        id=f"ev-{battery_wh}", locations=locations, orders=orders,
        vehicles=(Vehicle(
            id="V1", capacities={"kg": 100},
            shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
            start_location_id="D", end_location_id="D",
            battery_wh=battery_wh, consumption_wh_per_km=250,
            charger_locations=frozenset({"CH"}), charging_curve=curve()),),
        matrix=TravelMatrix(
            version="ev",
            durations=tuple(tuple(m // 10 for m in row) for row in metres),
            distances=metres))


def clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def show(timeline) -> None:
    print(f"      {'step':9s} {'where':6s} {'arrive':>7s} {'leave':>7s} "
          f"{'battery':>8s}")
    for step in timeline:
        charge = ("--" if step.soc_after_ppt is None
                  else f"{step.soc_after_ppt / 10:.0f}%")
        print(f"      {step.type:9s} {step.location_id:6s} "
              f"{clock(step.arrival):>7s} {clock(step.departure):>7s} "
              f"{charge:>8s}")


def the_curve() -> None:
    heading("1.", "Why the last fifth of a battery costs what it does")
    battery = 50_000
    print("\n   fifths of a 50 kWh battery, and what each costs to put in:\n")
    print(f"      {'from':>6s} {'to':>6s} {'takes':>8s}")
    for low in (0, 200, 400, 600, 800):
        seconds = charge_seconds(battery, curve(), low, low + 200)
        print(f"      {low // 10:5d}% {(low + 200) // 10:5d}% "
              f"{seconds // 60:5d} min")
    print("\n   A model with a constant rate cannot prefer the shorter stop,")
    print("   so it plans the wrong one and is confident about the wrong")
    print("   arrival time for every stop after it.")


def beyond_the_battery(problem: Problem) -> None:
    heading("2.", "A round the van cannot finish")
    timeline = build_timeline(problem, "V1", ORDERS)
    show(timeline)
    flat = min(step.soc_after_ppt for step in timeline)
    print(f"\n   240 km on 200 km of range: it ends {-flat / 10:.0f}% past")
    print("   empty. The state of charge is in the timeline, so the shortfall")
    print("   has a step to point at rather than being a total that is wrong.")


def the_stop(problem: Problem):
    heading("3.", "Where the plan puts the charge, and how full")
    charges = plan_charging(problem, "V1", ORDERS)
    for index, stop in sorted(charges.items()):
        print(f"\n   before {ORDERS[index]}: charge at {stop.location_id} to "
              f"{stop.to_soc_ppt / 10:.0f}%")
    print(f"   ({RESERVE_PPT / 10:.0f}% reserve, and no more than the rest of")
    print("   the round needs -- filling to 100% would spend the slow end of")
    print("   the curve on charge nobody is going to use.)\n")
    timeline = build_timeline(problem, "V1", ORDERS, charges=charges)
    show(timeline)
    report = verify(problem, Solution(
        problem_id=problem.id, status="FEASIBLE",
        routes=(Route(vehicle_id="V1", steps=timeline),), unassigned=()))
    print(f"\n   INV-16, checked by the verifier rather than by the planner: "
          f"{'passes' if report.ok else 'FAILS'}")
    return charges


def why_a_step(problem: Problem, charges) -> None:
    heading("4.", "Why the charge is a step and not a line in a report")
    without = build_timeline(problem, "V1", ORDERS)
    with_charge = build_timeline(problem, "V1", ORDERS, charges=charges)
    plug = next(s for s in with_charge if s.type == "CHARGE")
    plugged = plug.departure - plug.start_service
    print(f"\n   {plugged // 60} minutes on the charger, and every stop after "
          f"it moves:\n")
    print(f"      {'stop':6s} {'without':>8s} {'with':>8s} {'later by':>9s}")
    for order_id in ORDERS:
        a = next(s for s in without if s.order_id == order_id)
        b = next(s for s in with_charge if s.order_id == order_id)
        print(f"      {order_id:6s} {clock(a.arrival):>8s} "
              f"{clock(b.arrival):>8s} {(b.arrival - a.arrival) // 60:6d} min")
    print("\n   A time window on any of those can now notice. Charging added")
    print("   to a total at the end is a number; charging in the timeline is")
    print("   a constraint.")


def the_impossible_round() -> None:
    heading("5.", "The round that is not a planning problem")
    try:
        plan_charging(a_round(battery_wh=30_000), "V1", ORDERS)
    except NoChargerReachable as refusal:
        print(f"\n   a 30 kWh van on the same work:\n\n      {refusal}")
    print("\n   Not a plan with a flat battery in it, and not a plan quietly")
    print("   missing a stop. The fleet is wrong for the work, which is a")
    print("   thing a dispatcher can still fix at eight in the morning.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-20 and INV-16. Chargers and curve invented; corpus generated.")
    problem = a_round(battery_wh=50_000)
    the_curve()
    beyond_the_battery(problem)
    charges = the_stop(problem)
    why_a_step(problem, charges)
    the_impossible_round()
    print(f"\n{'=' * 72}")
    print("Range is not a capacity: a detour puts it back, on a curve.")
    print("So the search does not carry it -- the plan is repaired, and the")
    print("verifier is what says the repair worked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
