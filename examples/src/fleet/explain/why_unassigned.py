"""A diagnosis is half an answer. This is the other half.

Demonstrates the explanation service landed for E-60/T-60 (CON-5, FR-36, §9.4):

    vrp.explain     the rationale, the marginal cost, `would_fit_if`
    vrp.diagnose    T-14's reason codes, which this turns into edits
    vrp.allocate    T-44's per-vehicle side of the same question

CON-5 is unusually direct about why this exists: "Every route plan MUST be able
to answer, per order: why was I assigned to this vehicle, in this position, at
this time? Every rejection MUST answer: which constraint made me infeasible, and
what would have to change? Dispatchers reject plans they cannot explain, and
unexplainable plans are silently overridden -- which destroys the benefit."

So the bar is not that an explanation exists. "Time window problem" is an
explanation and it is useless.

Four things, in order:

1. **Why this van, this position, this time**, with the detour it cost.

2. **What would have to change**, as a concrete edit rather than advice.

3. **Where it declines to guess**, which is the part that keeps it trustworthy.

4. **Twenty queries**, the closest thing to T-60's usability test that can be
   run without dispatchers.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/explain/why_unassigned.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.explain import explain, explain_assignment
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

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 1800


def clock(seconds: int) -> str:
    return f"{8 + seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def instance(stops: int = 3, **overrides) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    windows = overrides.get("windows", {})
    return Problem(
        id="explain",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(
            Order(id=f"O{i}", kind="JOB",
                  quantities=overrides.get("quantities", {"kg": 1}),
                  required_skills=overrides.get("skills", frozenset()),
                  delivery=StopSpec(location_id=f"C{i}",
                                    time_windows=(windows.get(f"O{i}", DAY),),
                                    service_fixed=60))
            for i in range(1, size)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D",
                          cost_per_metre=1),),
        matrix=TravelMatrix(version="e", durations=grid, distances=grid))


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


def show_rationale() -> None:
    print("\n1. Why this van, this position, this time (CON-5)")
    problem = instance()
    current = plan(problem, {"V1": ["O1", "O2", "O3"]})

    for order_id in ("O1", "O3"):
        why = explain_assignment(problem, current, order_id)
        print(f"   {order_id}:")
        for line in why.because:
            print(f"     - {line}")
        print(f"     arrives {clock(why.arrival)}, marginal cost "
              f"{why.marginal_cost:,} m")

    print("   O1 costs nothing extra: it sits directly on the way to O3, so")
    print("   dropping it would save no distance at all. That is worth showing")
    print("   rather than hiding -- a dispatcher asking to move a free stop is")
    print("   asking for nothing back, and the number says so.")
    print("   The marginal cost is the detour: what the route would have saved")
    print("   by not going there. Checkable on a map, which is the only reason")
    print("   to believe it. The vehicle-level question -- what would we save")
    print("   by not having this van -- is T-44's, and needs a re-solve.")


def show_what_to_change() -> None:
    print("\n2. What would have to change (CON-5's second question)")
    cases = {
        "window closes before anyone can arrive":
            instance(windows={"O2": TimeWindow(start=0, end=600)}),
        "order heavier than any van":
            instance(quantities={"kg": 500}),
        "needs equipment no van has":
            instance(skills=frozenset({"TAIL_LIFT"})),
    }
    stocked = instance(quantities={"kg": 40})
    cases["depot short of stock"] = replace(stocked, locations=(
        replace(stocked.location("D"), inventory={"kg": 10}),
        *stocked.locations[1:]))

    for label, problem in cases.items():
        report = explain(problem, plan(problem, {"V1": []}))
        for order_id, rejection in report.rejected.items():
            if not rejection.would_fit_if:
                continue
            print(f"   {label}")
            print(f"     {order_id}  {rejection.reason_code}")
            for change in rejection.would_fit_if:
                shown = (clock(change.to) if change.change.endswith("_end")
                         else change.to)
                print(f"     would fit if:  {change.change} -> {shown}")
            break

    print("   Not \"widen the window\" but \"widen it to 09:00\". A dispatcher")
    print("   handed a diagnosis and no prescription still has all the work in")
    print("   front of them, which is how §9.4's own example is phrased:")
    print("   \"Earliest arrival 14:12... window closes 13:30\".")


def show_where_it_declines() -> None:
    print("\n3. Where it declines to guess")
    problem = instance()
    current = plan(problem, {"V1": ["O1", "O2"]})
    rejection = explain(problem, current).rejected["O3"]

    print(f"   O3  {rejection.reason_code}")
    print(f"     {rejection.explanation[:64]}...")
    print(f"     would fit if: {rejection.would_fit_if or 'nothing to suggest'}")
    print("   O3 is perfectly servable on its own -- pre-flight says so. It went")
    print("   unassigned because the day filled up, and §6.5 lists")
    print("   FLEET_EXHAUSTED among the codes that need a solve to establish.")
    print("   Offering a fix here would mean guessing with a straight face, and")
    print("   E-14's argument applies to a wrong prescription exactly as much")
    print("   as to a wrong diagnosis: it costs somebody an afternoon.")


def show_twenty_queries() -> None:
    print("\n4. Twenty queries (T-60's definition of done, as far as it goes)")
    shapes = []
    for closes in (1, 600, 1_200, LEG, LEG + 1):
        shapes.append(("window", instance(windows={
            "O2": TimeWindow(start=0, end=closes)})))
    for kg in (150, 300, 500, 900):
        shapes.append(("capacity", instance(quantities={"kg": kg})))
    for skill in ("TAIL_LIFT", "FRIDGE", "ADR", "CRANE"):
        shapes.append(("skill", instance(skills=frozenset({skill}))))
    for stock in (1, 5, 20):
        base = instance(quantities={"kg": 40})
        shapes.append(("stock", replace(base, locations=(
            replace(base.location("D"), inventory={"kg": stock}),
            *base.locations[1:]))))
    for stops in (2, 3, 4, 5):
        shapes.append(("mixed", instance(stops=stops, windows={
            "O2": TimeWindow(start=0, end=1)})))

    answered = {}
    for kind, problem in shapes:
        report = explain(problem, plan(problem, {"V1": []}))
        for rejection in report.rejected.values():
            if rejection.would_fit_if:
                answered.setdefault(kind, 0)
                answered[kind] += 1
                break

    for kind, count in sorted(answered.items()):
        print(f"   {kind:<10}{count:>3} of {sum(1 for k, _ in shapes if k == kind)}"
              f" answered with a concrete edit")
    print(f"   total: {sum(answered.values())}/20")
    print("   T-60 asks for \"a dispatcher usability test passed on 20 real")
    print("   queries\", and a usability test needs dispatchers. This is the")
    print("   half that can be checked without them: twenty distinct shapes,")
    print("   every one answered with a vehicle, an instant, a quantity or a")
    print("   skill -- never a bare category. A service that said \"time window")
    print("   problem\" twenty times would pass a coverage count and fail the")
    print("   requirement. The other half is still owed.")


def main() -> int:
    show_rationale()
    show_what_to_change()
    show_where_it_declines()
    show_twenty_queries()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
