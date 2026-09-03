"""Eleven o'clock, and the morning is not up for discussion.

Demonstrates the committed-state manager landed for E-50/T-50 (DYN-4, AC-2.2,
§8.3):

    vrp.committed   the prefix, the freeze, and what moved
    vrp.verify      INV-8, which has enforced both lock kinds since T-29
    vrp.model       §6.6's `FIX_ROUTE_PREFIX` and `FREEZE_UNTIL`

US-2 is the requirement in a sentence: "when a vehicle breaks down at 11:00, I
re-optimise only the affected and nearby work while everything already executed
or committed stays fixed." AC-2.2 names the hard part -- "No stop already
visited or currently en route is moved."

Both lock kinds have existed since T-29 and INV-8 has enforced them since. What
was missing was the thing that produces them, so a re-optimisation at 11:00 was
free to reorder the morning and nothing in the system objected.

Four things, in order:

1. **What is committed, through the day.** The prefix grows and never
   contradicts itself.

2. **En route counts.** A van three minutes from a stop has not visited it, and
   moving it means a driver turning around in the street.

3. **The locks, and the plan they refuse.** A re-optimisation that reorders the
   morning is unremarkable until these locks exist.

4. **A day replayed.** T-50's definition of done: no executed stop ever moves.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/dynamic/committed_state.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.committed import Execution, commit_locks, committed_prefix, moved_since
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
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)
LEG = 900
ROUTES = {"V1": ["O1", "O2", "O3"], "V2": ["O4", "O5", "O6"]}


def clock(seconds: int) -> str:
    return f"{6 + seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def instance(stops: int = 6, vans: int = 2) -> Problem:
    """A morning's real deliveries, some of them already made.

    Real coordinates and real service times, so the distances a re-plan trades
    against are ones a driver would recognise.
    """
    locations, matrix, deliveries, _depot = dataset.planar_sites(
        stops, strategy="spread", name="commit")
    return Problem(
        id="commit", locations=locations,
        orders=tuple(
            Order(id=f"O{i + 1}", kind="JOB", quantities={"kg": 1},
                  delivery=StopSpec(location_id=f"C{i + 1}",
                                    time_windows=(DAY,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                               start_location_id="D", end_location_id="D")
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
                              start_service=now, departure=now + 300))
            now, here = now + 300, there
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


def show_the_day(problem: Problem, current: Solution) -> None:
    print("\n1. What is committed, as the day goes on")
    print(f"   {'time':>7}{'V1 committed':>28}{'V2 committed':>28}")
    for now in range(0, 5 * 3600, 1800):
        row = [committed_prefix(problem, route, now, include_en_route=True)
               for route in current.routes]
        print(f"   {clock(now):>7}{row[0]!s:>28}{row[1]!s:>28}")
    print("   It only ever grows. That is the property a re-optimisation rests")
    print("   on: a manager that recomputed the prefix from a changed plan")
    print("   would lose the earlier half, and be right at 11:00 and wrong at")
    print("   13:00.")


def show_en_route(problem: Problem, current: Solution) -> None:
    print("\n2. En route counts as committed (AC-2.2)")
    route = current.routes[0]
    for now in (500, 2_000):
        served = committed_prefix(problem, route, now)
        held = committed_prefix(problem, route, now, include_en_route=True)
        print(f"   {clock(now)}  served {served}   committed {held}")
    print("   At 06:08 the van has served nothing and is already driving to")
    print("   O1; at 06:33 it has served O1 and is driving to O2. Neither O1")
    print("   nor O2 has been visited at the moment it becomes committed, and")
    print("   both are committed all the same. Moving one means a driver")
    print("   turning around in the street. AC-2.2 names both states in one")
    print("   breath, and a manager pinning only completed work would pass")
    print("   every test written against completed work.")

    behind = Execution(completed={"V1": ("O1",)}, en_route={"V1": "O2"})
    late = committed_prefix(problem, route, now=5 * 3600,
                            include_en_route=True, execution=behind)
    print(f"   telematics at 11:00 on a van running late: {late}")
    print("   The plan's clock says all three stops are done. The van says")
    print("   one, with a second in progress.")
    print("   Telematics wins, or the manager pins work that never happened.")


def show_the_locks(problem: Problem, current: Solution) -> None:
    print("\n3. The locks, and the plan they refuse")
    now = 2_000
    locks = commit_locks(problem, current, now)
    for lock in locks:
        detail = (f"{lock.vehicle_id} begins {list(lock.order_ids)}"
                  if lock.kind == "FIX_ROUTE_PREFIX"
                  else f"nothing new before {clock(lock.instant)}")
        print(f"   {lock.kind:<18} {detail}")

    locked = replace(problem, locks=locks)
    honest = verify(locked, plan(locked, ROUTES))
    reordered = verify(locked, plan(locked, {"V1": ["O2", "O1", "O3"],
                                             "V2": ["O4", "O5", "O6"]}))
    print(f"   the plan as it stands verifies: {honest.ok}")
    for violation in reordered.violations:
        if violation.invariant == "INV-8":
            print(f"   swapping O1 and O2: {violation.invariant} "
                  f"{violation.detail}")
    print("   Without these locks that swap is unremarkable -- it is a little")
    print("   shorter, and every other invariant passes. With them it is the")
    print("   optimiser rewriting the past.")
    print("   The freeze is the second half and not a duplicate: the prefix")
    print("   pins what each van has *done*, the freeze stops the optimiser")
    print("   filling the morning around it with new work.")


def show_replay(problem: Problem, current: Solution) -> None:
    print("\n4. A day replayed, re-planning at every epoch")
    history: dict[str, list[str]] = {"V1": [], "V2": []}
    churn = 0

    for now in range(0, 7 * 3600, 1800):
        previous = current
        for route in current.routes:
            prefix = committed_prefix(problem, route, now,
                                      include_en_route=True)
            earlier = history[route.vehicle_id]
            assert prefix[:len(earlier)] == earlier, (now, earlier, prefix)
            history[route.vehicle_id] = prefix

        # Re-plan the open work as destructively as the locks allow.
        open_work = {}
        for route in current.routes:
            ids = [step.order_id for step in route.steps if step.order_id]
            fixed = history[route.vehicle_id]
            open_work[route.vehicle_id] = fixed + list(
                reversed(ids[len(fixed):]))
        current = plan(problem, open_work)
        churn += len(moved_since(previous, current))

    print(f"   {'vehicle':<9}{'executed, in order':>30}")
    for vehicle_id, done in sorted(history.items()):
        print(f"   {vehicle_id:<9}{done!s:>30}")
    print(f"   stops that changed vehicle across the day: {churn}")
    print("   The open half was reversed at every epoch and nothing already")
    print("   committed moved once. That is T-50's definition of done -- \"no")
    print("   executed stop ever moves\" -- and note the executed order is not")
    print("   the opening plan's: before anything is committed, reversing is")
    print("   legal and is re-optimisation working, not a violation.")


def main() -> int:
    problem = instance()
    current = plan(problem, ROUTES)
    show_the_day(problem, current)
    show_en_route(problem, current)
    show_the_locks(problem, current)
    show_replay(problem, current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
