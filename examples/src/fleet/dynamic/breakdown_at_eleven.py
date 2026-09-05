"""A van goes down at 11:00, and most of the day does not move.

Demonstrates the trigger engine and locked re-optimisation landed for
E-56/T-56 (DYN-5, AC-2.1, AC-2.3, §8.3, §8.4):

    vrp.triggers    the four events, the T1 scope, the delta
    vrp.committed   T-50's locks, which hold the morning in place
    vrp.evaluator   the cost and lateness the delta is measured in

US-2, in one sentence: "when a vehicle breaks down at 11:00, I re-optimise only
the affected and nearby work while everything already executed or committed
stays fixed."

Four things, in order:

1. **The four events DYN-5 names**, and what each one touches.

2. **How the budget is met.** AC-2.1 allows thirty seconds with 90% locked, and
   §8.4 says the method is "locked LNS on affected + neighbouring routes only".
   The budget is met by not re-solving the plan.

3. **The delta, not just the plan.** AC-2.3 asks for stops moved, cost change
   and new lateness -- three numbers, because they move independently.

4. **Why §8.3 insists on it.** "A 0.5% cost gain that reshuffles half the plan
   at 14:00 is a net loss", and a response carrying only a plan hides that.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/dynamic/breakdown_at_eleven.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

# Importing config puts OSRM_API_URL into the environment, so the
# gateway this example now requires is the one `examples/.env` names.
import config  # noqa: F401
import dataset

from vrp.model import (
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.triggers import Trigger, affected_routes, reoptimise

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def clock(seconds: int) -> str:
    return f"{8 + seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def instance(stops: int = 12, vans: int = 4) -> Problem:
    """A day's work around the Guadalupe depot, split across a small fleet.

    Real coordinates and real service times, so the distances a re-plan trades
    against are ones a driver would recognise.
    """
    locations, matrix, deliveries, _depot = dataset.road_sites(
        stops, strategy="spread", name="react")
    return Problem(
        id="react", locations=locations,
        orders=tuple(
            Order(id=f"O{i + 1}", kind="JOB", quantities={"kg": 1},
                  delivery=StopSpec(location_id=f"C{i + 1}",
                                    time_windows=(DAY,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                               start_location_id="D", end_location_id="D", cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=matrix)

def plan(problem: Problem, assignment: dict[str, list[str]]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        now, here = 0, index["D"]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            now += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=now,
                              start_service=now, departure=now + 60))
            now, here = now + 60, there
        now += problem.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=now,
                          start_service=now, departure=now))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in problem.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


ROUTES = {"V1": ["O1", "O2", "O3"], "V2": ["O4", "O5", "O6"],
          "V3": ["O7", "O8", "O9"], "V4": ["O10", "O11", "O12"]}


def show_the_triggers(problem: Problem, current: Solution) -> None:
    print("\n1. The four events DYN-5 names")
    print(f"   {'trigger':<18}{'subject':<10}{'routes opened':<20}")
    for kind, kwargs in (("BREAKDOWN", {"vehicle_id": "V2"}),
                         ("CANCELLATION", {"order_id": "O8"}),
                         ("ETA_DRIFT", {"vehicle_id": "V1"}),
                         ("PRIORITY_ORDER", {"order_id": "O11"})):
        trigger = Trigger(kind, at=3 * HOUR, **kwargs)
        touched = affected_routes(problem, current, trigger, neighbours=1)
        subject = kwargs.get("vehicle_id") or kwargs.get("order_id")
        print(f"   {kind:<18}{subject:<10}{sorted(touched)!s:<20}")
    print("   Event driven, not on a timer. A trigger engine that fired hourly")
    print("   would be idle when the van broke and busy when nothing had")
    print("   happened.")


def show_the_budget() -> None:
    print("\n2. How the thirty-second budget is met (AC-2.1, §8.4)")
    big = instance(stops=100, vans=20)
    routes = {f"V{n}": [f"O{i}" for i in range(5 * n - 4, 5 * n + 1)]
              for n in range(1, 21)}
    current = plan(big, routes)

    timings = []
    for run in range(20):
        started = time.monotonic()
        response = reoptimise(big, current,
                              Trigger("BREAKDOWN", 0,
                                      vehicle_id=f"V{run % 20 + 1}"), now=0)
        timings.append(time.monotonic() - started)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]

    print("   100 stops, 20 routes, one van down")
    print(f"   locked: {response.locked_share / 10:.0f}% of the plan")
    print(f"   p95 {p95 * 1000:.0f} ms over 20 runs, worst "
          f"{max(timings) * 1000:.0f} ms, budget 30,000 ms")
    print("   The budget is met by not re-solving the plan. §8.4 scopes this")
    print("   tier to \"affected + neighbouring routes only\", so eighteen of")
    print("   twenty routes are never looked at. A re-optimisation that touched")
    print("   everything would be a fresh solve wearing a different name, and")
    print("   would blow the budget on any fleet worth the trouble.")


def show_the_delta(problem: Problem, current: Solution) -> None:
    print("\n3. What comes back (AC-2.3)")
    # Early in the day, when there is still work left to move. These routes
    # finish inside two hours, so a breakdown at 11:00 displaces nothing at all
    # -- correct, and a vacuous thing to print as a demonstration.
    response = reoptimise(problem, current,
                          Trigger("BREAKDOWN", 30 * 60, vehicle_id="V2"),
                          now=30 * 60)
    delta = response.delta

    print(f"   stops moved:  {delta.churn}")
    for order_id, (was, now) in sorted(delta.moved.items()):
        print(f"      {order_id:<5} {was} -> {now or 'unassigned'}")
    print(f"   cost:         {delta.cost_before:,} -> {delta.cost_after:,} "
          f"({delta.cost_change:+,})")
    print(f"   lateness:     {delta.lateness_before:,} -> "
          f"{delta.lateness_after:,}")
    print("   Three numbers, because they move independently. A cheaper plan")
    print("   that reshuffles half the fleet and a cheaper plan that touches")
    print("   nothing are different answers, and a single total cannot tell a")
    print("   dispatcher which one they are being handed.")
    print("   The moved stops are named rather than counted: a count says how")
    print("   bad it is, the names say who to ring.")


def show_why_it_matters(problem: Problem, current: Solution) -> None:
    print("\n4. Why §8.3 insists on the delta")
    quiet = reoptimise(problem, current,
                       Trigger("ETA_DRIFT", 30 * 60, vehicle_id="V1"),
                       now=30 * 60, neighbours=0)
    print(f"   an ETA drift that changes nothing: {quiet.delta.churn} stops "
          f"moved, cost {quiet.delta.cost_change:+,}")
    print(f"   worth accepting? {quiet.worth_it}")
    print("   §8.3: \"A 0.5% cost gain that reshuffles half the plan at 14:00")
    print("   is a net loss.\" A response carrying only a plan hides that trade")
    print("   entirely, and the dispatcher accepts it without being asked.")
    print("   Note also what did not happen: everything already committed is")
    print("   still exactly where it was. T-50 built those locks and this is")
    print("   the first thing that could have ignored them -- §8.3 says")
    print("   re-optimisation \"MUST respect FREEZE_UNTIL and never move")
    print("   executed work\", and a van already at the door does not turn")
    print("   around because the optimiser found something tidier.")


def main() -> int:
    problem = instance()
    current = plan(problem, ROUTES)
    show_the_triggers(problem, current)
    show_the_budget()
    show_the_delta(problem, current)
    show_why_it_matters(problem, current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
