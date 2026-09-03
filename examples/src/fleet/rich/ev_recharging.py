"""A van that has to stop for electricity, and a plan that knows when.

Demonstrates EV range and recharging landed for E-41/T-41 (FR-20, INV-16):

    vrp.battery    consumption, and the charging curve
    vrp.electric   where a round has to stop, or why it cannot be driven

`FR-20` is the backlog's only `COULD` and was its only task with no data
source: nobody here has charger locations or a manufacturer's charging curve.
Its definition of done asks for range never violated on a **generated** EV
corpus, and generating one needs neither -- which is why it was buildable.

The round is real. Three deliveries in Guanacaste, roughly 190 km from the
Guadalupe depot in San Jose, which is a 430 km day and the kind of work a
regional distributor actually sends a van on. The chargers are the fleet's own
depots: a distributor with six sites has electricity at all of them, so the
question is not where to build a charger but which one the round can reach.

Five things, in order:

1. **The curve, which is the requirement.** A battery that charged at a
   constant rate would make the charging time a division. Real cells taper
   near the top, which is why a driver charges to eighty percent and drives on.

2. **A round beyond the battery.** The state of charge is in the timeline, so
   the shortfall is visible at the step where it happens rather than as a total
   that is merely wrong.

3. **The stop, placed.** At whichever depot the van can still reach, and
   charged to what the rest of the round needs rather than to full, because the
   last fifth is the expensive fifth.

4. **Why it has to be a step.** The charge pushes every later arrival by its
   own duration. That is the difference between modelling the constraint and
   accounting for it: an hour in a report cannot break a time window.

5. **The round nobody can drive.** A smaller battery makes the work impossible
   rather than the plan wrong, and it is refused by name -- with two different
   refusals, because "no charger in range from here" and "flat even after
   charging everywhere" are different problems for a dispatcher.

Runs offline, against the committed corpus slice. The stops, their spacing and
their service times are the corpus's; the charging curve and the decision to
put a charger at every depot are invented, which is what the DoD asked for.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/ev_recharging.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.battery import FULL_PPT, ChargingCurve, charge_seconds
from vrp.electric import RESERVE_PPT, NoChargerReachable, plan_charging
from vrp.evaluator import build_timeline
from vrp.matrix import PlanarMatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.verify import verify

HOUR = 3600
KM = 1000
STOPS = 3
ORDERS = [f"O{i}" for i in range(1, STOPS + 1)]
# A large electric van, and what one uses hauling freight through hills.
BATTERY_WH = 75_000
CONSUMPTION_WH_PER_KM = 250


def curve() -> ChargingCurve:
    """Fast to eighty percent, then slow -- the shape that makes the decision."""
    return ChargingCurve(bands=((800, 60_000), (FULL_PPT, 20_000)))


def geometry() -> tuple:
    """The real round, and the depots standing in as chargers.

    Returns:
        `(locations, matrix, deliveries, depot, charger_names)`, where the
        chargers are every depot except the one the van starts from.
    """
    locations, _, deliveries, depot = dataset.planar_sites(
        STOPS, "furthest", "ev")
    corpus = dataset.load()
    others = [d for d in corpus.depots if d["name"] != depot["name"]]
    extra = tuple(
        Location(id=f"CH{i}", lat=d["latitude"], lon=d["longitude"],
                 matrix_index=len(locations) + i - 1)
        for i, d in enumerate(others, 1))
    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    points += [(d["latitude"], d["longitude"]) for d in others]
    return (locations + extra, _matrix_over(points, depot), deliveries, depot,
            {f"CH{i}": d["name"] for i, d in enumerate(others, 1)})


def _matrix_over(points: list[tuple[float, float]], depot: dict) -> PlanarMatrix:
    """Degrees to kilometres about the depot, then a planar matrix.

    Args:
        points: `(latitude, longitude)` for every site, the depot first.
        depot: The site the round starts from.

    Returns:
        A matrix whose distances are straight lines -- shorter than the road's,
        and by different amounts in different places.
    """
    lat_km = 110.57
    lon_km = 111.32 * math.cos(math.radians(depot["latitude"]))
    coordinates = tuple(((lon - depot["longitude"]) * lon_km,
                         (lat - depot["latitude"]) * lat_km)
                        for lat, lon in points)
    return PlanarMatrix(version="ev-v1", coordinates=coordinates)


LOCATIONS, MATRIX, DELIVERIES, DEPOT, CHARGERS = geometry()


def tour_metres() -> int:
    """What the round costs if it is driven in the order it is given."""
    legs = MATRIX.distance(0, 1) + MATRIX.distance(STOPS, 0)
    return legs + sum(MATRIX.distance(i, i + 1) for i in range(1, STOPS))


def a_round(battery_wh: int = BATTERY_WH,
            consumption_wh_per_km: int = CONSUMPTION_WH_PER_KM) -> Problem:
    """The Guanacaste round, for a van of a given battery.

    Args:
        battery_wh: Usable capacity in watt-hours.
        consumption_wh_per_km: What the van draws, loaded.

    Returns:
        A problem whose chargers are the fleet's other depots.
    """
    day = TimeWindow(start=0, end=20 * HOUR)
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}",
                                service_fixed=d["service_minutes"] * 60,
                                time_windows=(day,)))
        for i, d in enumerate(DELIVERIES, 1))
    vehicle = Vehicle(
        id="V1", capacities={"kg": 100}, shift=TimeWindow(start=6 * HOUR,
                                                          end=22 * HOUR),
        start_location_id="D", end_location_id="D", battery_wh=battery_wh,
        consumption_wh_per_km=consumption_wh_per_km,
        charger_locations=frozenset(CHARGERS), charging_curve=curve())
    return Problem(id=f"ev-{battery_wh}", locations=LOCATIONS, orders=orders,
                   vehicles=(vehicle,), matrix=MATRIX)


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
    print(f"\n   fifths of a {BATTERY_WH // 1000} kWh battery, and what each "
          "costs to put in:\n")
    print(f"      {'from':>6s} {'to':>6s} {'takes':>8s}")
    for low in (0, 200, 400, 600, 800):
        seconds = charge_seconds(BATTERY_WH, curve(), low, low + 200)
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
    reach = BATTERY_WH // CONSUMPTION_WH_PER_KM
    print(f"\n   {tour_metres() // KM} km on {reach} km of range: it ends "
          f"{-flat / 10:.0f}% past empty.")
    print("   The state of charge is in the timeline, so the shortfall has a")
    print("   step to point at rather than being a total that is wrong.")


def the_stop(problem: Problem):
    heading("3.", "Where the plan puts the charge, and how full")
    charges = plan_charging(problem, "V1", ORDERS)
    for index, stop in sorted(charges.items()):
        print(f"\n   before {ORDERS[index]}: charge at "
              f"{CHARGERS[stop.location_id]} to {stop.to_soc_ppt / 10:.0f}%")
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


def what_battery_buys(battery: int) -> str:
    """One line for one van: refused by name, or where it has to stop.

    Args:
        battery: Usable capacity in watt-hours.

    Returns:
        A description of the day that van gets.
    """
    try:
        charges = plan_charging(a_round(battery_wh=battery), "V1", ORDERS)
    except NoChargerReachable as refusal:
        return str(refusal)
    if not charges:
        return "drives it on one charge, no stop"
    where = ", ".join(f"{CHARGERS[stop.location_id]} to "
                      f"{stop.to_soc_ppt / 10:.0f}%"
                      for _, stop in sorted(charges.items()))
    return f"{len(charges)} stop: {where}"


def the_impossible_round() -> None:
    heading("5.", "What the same work asks of the fleet")
    print(f"\n   {tour_metres() // KM} km, and every van that could be sent "
          "at it:\n")
    for battery in (30_000, 50_000, BATTERY_WH, 120_000):
        reach = battery // CONSUMPTION_WH_PER_KM
        print(f"      {battery // 1000:3d} kWh ({reach:3d} km)  "
              f"{what_battery_buys(battery)}")
    print("\n   The two refusals are different sentences because they are")
    print("   different problems. The 50 kWh van is stranded somewhere")
    print("   specific and a charger nearer that stop would fix it; the")
    print("   30 kWh van cannot do this work at any charge, and no charger")
    print("   helps. Neither is a plan with a flat battery in it, and neither")
    print("   is a plan quietly missing a stop -- which is what a dispatcher")
    print("   needs at eight in the morning, when hiring a diesel van is")
    print("   still possible and un-stranding a driver is not.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print(f"\nFR-20 and INV-16. Real stops from {DEPOT['name']}; "
          "chargers and curve invented.")
    problem = a_round()
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
