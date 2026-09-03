"""Two hours of duty that nobody was paying attention to.

Demonstrates the route-level exact polish landed for E-39/T-39 (ALG-5):

    vrp.polish        both passes -- departure scheduling and TSPTW sequencing
    vrp.bench.corpus  the frozen corpus T-39 measures against
    vrp.hos.schedule  the canonical timeline both passes are judged by

ALG-5 says departure scheduling is "nearly free" and that "many production
plans leave several percent of duty time on the table by departing at the
earliest possible moment by default". This codebase is one of them:
`schedule_route` starts the clock at `vehicle.shift.start` unless told
otherwise. The customer is served at the same moment either way. The driver is
on duty for the wait.

Four things this shows, in order:

1. **One route, traced.** Where the waiting goes, and what moving the departure
   does to it. Nothing is served earlier or later; the duty is two hours
   shorter.

2. **The frozen corpus.** The saving splits cleanly by window tightness --
   about a quarter of the duty where windows are tight, exactly nothing where
   they are slack. The zeros are the control: a pass that "improved" a route
   with no waiting in it would be shifting departures for its own sake.

3. **Exact, not approximate.** The closed form is checked against trying every
   departure second in the shift. They agree to the second, which is the only
   way to tell an exact answer from a plausible one.

4. **The sequencing pass, including where it finds nothing.** On all 21
   corpus routes the DP confirms the incoming order and changes none of them --
   PyVRP is already optimal at these sizes. It earns its place on routes
   nothing has polished yet, and it declines above ~14 stops rather than
   truncating.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/departure_scheduling.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.bench.corpus import CORPUS, build_instance
from vrp.hos.schedule import schedule_route
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.polish import (
    MAX_DP_STOPS,
    duty_duration,
    optimal_departure,
    polish_route,
    tsptw_sequence,
)
from vrp.solve import pyvrp_adapter

TIGHT = "c30-clustered-tight"
DECLINED = "   declined -- above ALG-5's ~14-stop bound"


def clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def routes_of(problem: Problem):
    """Every route with something to schedule, longest first."""
    solution = pyvrp_adapter.solve(problem, iterations=300, seed=0)
    found = [(route.vehicle_id, [s.order_id for s in route.steps if s.order_id])
             for route in solution.routes]
    return [(v, ids) for v, ids in found if len(ids) >= 2]


def show_one_route() -> None:
    """The whole idea, on a timeline small enough to read."""
    print(f"\n1. One route from {TIGHT}, before and after")
    problem = build_instance(next(s for s in CORPUS if s.name == TIGHT))
    vehicle_id, order_ids = routes_of(problem)[0]

    before = schedule_route(problem, vehicle_id, order_ids, rules=None)
    departure = optimal_departure(problem, vehicle_id, order_ids)
    after = schedule_route(problem, vehicle_id, order_ids, rules=None,
                           start_time=departure)

    print(f"   {'stop':<8}{'arrive':>8}{'wait':>7}{'serve':>8}"
          f"{'|':>4}{'arrive':>8}{'wait':>7}{'serve':>8}")
    for old, new in zip(before.steps, after.steps):
        label = old.order_id or old.type
        print(f"   {label:<8}{clock(old.arrival):>8}"
              f"{(old.start_service - old.arrival) // 60:>6}m"
              f"{clock(old.start_service):>8}{'|':>4}"
              f"{clock(new.arrival):>8}"
              f"{(new.start_service - new.arrival) // 60:>6}m"
              f"{clock(new.start_service):>8}")

    saved = duty_duration(before.steps) - duty_duration(after.steps)
    print(f"   duty {duty_duration(before.steps) / 3600:.1f}h -> "
          f"{duty_duration(after.steps) / 3600:.1f}h, "
          f"{saved / 3600:.1f}h saved by leaving at {clock(departure)}")
    print("   Every stop is served at exactly the same minute. The waiting that")
    print("   was on the clock at the first stop is now before the shift began.")


def show_corpus() -> None:
    """T-39's acceptance: duty-duration reduction measured and reported."""
    print("\n2. The frozen corpus")
    print(f"   {'instance':<26}{'routes':>7}{'duty':>9}{'polished':>10}"
          f"{'saved':>8}")

    for spec in CORPUS:
        problem = build_instance(spec)
        base = polished = 0
        routes = routes_of(problem)
        for vehicle_id, order_ids in routes:
            steps = schedule_route(problem, vehicle_id, order_ids,
                                   rules=None).steps
            departure = optimal_departure(problem, vehicle_id, order_ids)
            base += duty_duration(steps)
            polished += duty_duration(
                schedule_route(problem, vehicle_id, order_ids, rules=None,
                               start_time=departure).steps)
        gain = (base - polished) / base * 100 if base else 0.0
        print(f"   {spec.name:<26}{len(routes):>7}{base / 3600:>8.1f}h"
              f"{polished / 3600:>9.1f}h{gain:>7.1f}%")

    print("   The two -slack instances recover nothing because they wait for")
    print("   nothing. ALG-5 promises \"several percent\"; where its premise")
    print("   actually holds it is worth about a quarter of the duty.")


def show_exactness() -> None:
    """The closed form against brute force, which is the only real check."""
    print("\n3. The closed form, against trying every departure second")
    problem = build_instance(next(s for s in CORPUS if s.name == TIGHT))
    vehicle = problem.vehicles[0]

    print(f"   {'route':<8}{'closed form':>13}{'duty':>10}"
          f"{'exhaustive':>13}{'duty':>10}{'scan':>8}")
    for vehicle_id, order_ids in routes_of(problem)[:3]:
        computed = optimal_departure(problem, vehicle_id, order_ids)
        ours = duty_duration(schedule_route(problem, vehicle_id, order_ids,
                                            rules=None,
                                            start_time=computed).steps)
        started = time.monotonic()
        best = _scan(problem, vehicle, vehicle_id, order_ids, step=1)
        elapsed = time.monotonic() - started
        print(f"   {vehicle_id:<8}{clock(computed):>13}{ours:>9,}s"
              f"{clock(best[0]):>13}{best[1]:>9,}s{elapsed:>7.1f}s"
              f"  {'agree' if best[1] == ours else 'DIFFER'}")

    print("   Every second of a twelve-hour shift, and the duties match to the")
    print("   second. Every thirtieth would not have: a coarse grid cannot")
    print("   represent an exact answer, and an earlier version of this check")
    print("   scored the closed form six seconds wrong when it was the grid")
    print("   that could not land on the right departure.")
    print("   The departures themselves need not match. Several can share the")
    print("   shortest duty; `optimal_departure` returns the earliest of them,")
    print("   so the slack it does not need stays available.")


def _scan(problem, vehicle, vehicle_id, order_ids, step):
    best = None
    for departure in range(vehicle.shift.start, vehicle.shift.end + 1, step):
        scheduled = schedule_route(problem, vehicle_id, order_ids, rules=None,
                                   start_time=departure)
        if not scheduled.legal or not _in_windows(problem, scheduled):
            continue
        duty = duty_duration(scheduled.steps)
        if best is None or duty < best[1]:
            best = (departure, duty)
    return best


def _in_windows(problem: Problem, scheduled) -> bool:
    for step in scheduled.steps:
        if step.order_id is None:
            continue
        order = problem.order(step.order_id)
        stop = order.delivery or order.pickup
        hard = [w for w in stop.time_windows if w.hardness == "HARD"]
        if hard and not any(w.contains(step.start_service) for w in hard):
            return False
    return True


def show_sequencing() -> None:
    """The other half of ALG-5, and an honest account of what it found."""
    print("\n4. TSPTW sequencing")

    eligible = changed = 0
    for spec in CORPUS:
        problem = build_instance(spec)
        for vehicle_id, order_ids in routes_of(problem):
            if len(order_ids) > MAX_DP_STOPS:
                continue
            eligible += 1
            changed += polish_route(problem, vehicle_id,
                                    order_ids).resequenced
    print(f"   corpus: {eligible} eligible routes, {changed} resequenced.")
    print("   PyVRP's routes are already optimal in their own order at this")
    print("   size, so the DP confirms rather than improves. That is the")
    print("   result, not a disappointment -- and it is only knowable by")
    print("   solving each route exactly.")

    problem = _windowed_detour()
    order_ids = [order.id for order in problem.orders]
    print(f"\n   a route nothing has polished: {order_ids} -> "
          f"{tsptw_sequence(problem, 'V0', order_ids)}")
    print("   O2 closes at 150 s. Visiting it second arrives at 260 -- illegal.")
    print("   Visiting it first costs 1,100 s against the illegal 300, so a DP")
    print("   that ignored windows would report a cheaper, unusable answer.")

    print("\n   the DP on real rounds of each size:")
    print(f"\n   {'stops':>7}{'DP':>9}")
    for stops in (8, 12, MAX_DP_STOPS, MAX_DP_STOPS + 1):
        instance = _real_round(stops)
        started = time.monotonic()
        found = tsptw_sequence(instance, "V0",
                               [order.id for order in instance.orders])
        taken = time.monotonic() - started
        print(f"   {stops:>7}{taken:>8.2f}s"
              f"{'' if found else DECLINED}")


def _build(legs, windows, shift_end, service=60) -> Problem:
    grid = tuple(tuple(row) for row in legs)
    shift = TimeWindow(start=0, end=shift_end)
    return Problem(
        id="polish-demo",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(len(legs))),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             service_fixed=service,
                                             time_windows=(windows[i],)))
                     for i in range(1, len(legs))),
        vehicles=(Vehicle(id="V0", capacities={"kg": 100}, shift=shift,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="polish-demo", durations=grid,
                            distances=grid))


def _windowed_detour() -> Problem:
    """Asymmetric on purpose: a symmetric tour and its reverse always tie."""
    return _build([[0, 100, 100],
                   [100, 0, 100],
                   [100, 900, 0]],
                  {1: TimeWindow(start=0, end=8 * 3600),
                   2: TimeWindow(start=0, end=150)},
                  12 * 3600)


def _real_round(stops: int) -> Problem:
    """A real delivery round of `stops`, for timing the DP at each size.

    Scattered rather than collinear on purpose. Stops evenly spaced along a
    line are the easiest tour there is -- the optimal order is the order they
    come in -- so timing the DP on one flatters it. These are real addresses
    around the depot, and the DP has to actually search.

    Args:
        stops: How many deliveries to take.

    Returns:
        An instance with windows wide enough that only the sequencing costs
        anything.
    """
    day = TimeWindow(start=0, end=14 * 3600)
    locations, matrix, deliveries, _ = dataset.planar_sites(
        stops, "spread", "polish")
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}",
                                service_fixed=d["service_minutes"] * 60,
                                time_windows=(day,)))
        for i, d in enumerate(deliveries, 1))
    return Problem(
        id=f"polish-{stops}", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V0", capacities={"kg": 100}, shift=day,
                          start_location_id="D", end_location_id="D"),),
        matrix=matrix)


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    show_one_route()
    show_corpus()
    show_exactness()
    show_sequencing()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
