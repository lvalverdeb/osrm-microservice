"""Three plans that look perfect and cannot be driven.

Demonstrates the compatibility model landed for E-22/T-22 (FR-10, FR-11, §6.5):

    vrp.verify     INV-10, which judges a finished plan
    vrp.diagnose   pre-flight, which names the reason before any solve
    vrp.model      `required_skills`, `incompatible_with`, `access_classes`

§6.5 names three kinds of compatibility and they fail in three different ways.
What they have in common is that a plan violating any of them passes every
other invariant: the arithmetic is right, the windows are met, the loads fit.
It is simply undriveable, and nothing in the timeline says so.

The first kind is the interesting one, because the machinery already existed and
gave every appearance of working. `Vehicle.skills` and `Order.required_skills`
have been in the model since E-01 and pre-flight has checked them since E-14 --
but nothing checked a *finished plan*. A skill requirement that nothing enforces
is worse than no skill model at all: it invites people to rely on it.

Four things this shows, in order:

1. **Vehicle to order.** A tail-lift load on a van without one.
2. **Order to order.** Foodstuff sharing a compartment with hazardous goods.
3. **Vehicle to site.** A lorry sent where only a van may go, by class and by
   weight.
4. **Told twice, at different times.** Pre-flight names the reason before the
   solve; INV-10 catches it after. The two are independent on purpose.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/skills_and_access.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.diagnose import preflight
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
DEPOT = Location(id="D", lat=9.9, lon=-84.0, matrix_index=0)


def instance(orders, vehicles, locations=None) -> Problem:
    if locations is None:
        locations = (DEPOT,
                     Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1),
                     Location(id="C2", lat=9.92, lon=-84.0, matrix_index=2))
    size = len(locations)
    grid = tuple(tuple(abs(i - j) * 600 for j in range(size))
                 for i in range(size))
    return Problem(id="cmp", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="c", durations=grid,
                                       distances=grid))


def an_order(order_id: str, stop: str, **kwargs) -> Order:
    return Order(id=order_id, kind="JOB", quantities={"kg": 1},
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=60), **kwargs)


def a_van(vehicle_id: str = "V1", **kwargs) -> Vehicle:
    return Vehicle(id=vehicle_id, capacities={"kg": 100}, shift=DAY,
                   start_location_id="D", end_location_id="D", **kwargs)


def plan(problem: Problem, assignment: dict[str, list[str]]) -> Solution:
    """A timeline honest about travel, so only compatibility can fail."""
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index["D"]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            clock += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += problem.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in problem.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


def judge(label: str, problem: Problem, assignment: dict[str, list[str]]) -> None:
    report = verify(problem, plan(problem, assignment))
    if report.ok:
        print(f"   {label:<44} accepted")
        return
    for violation in report.violations:
        print(f"   {label:<44} {violation.invariant}: {violation.detail}")


def show_skills() -> None:
    """The kind that looked enforced and was not."""
    print("\n1. Vehicle to order — the load needs equipment the van lacks")
    lift = frozenset({"TAIL_LIFT"})

    plain = instance((an_order("O1", "C1", required_skills=lift),), (a_van(),))
    judge("tail-lift load on a plain van", plain, {"V1": ["O1"]})

    fitted = instance((an_order("O1", "C1", required_skills=lift),),
                      (a_van(skills=lift),))
    judge("the same load on a fitted van", fitted, {"V1": ["O1"]})

    print("   Every other invariant passes on the first plan. The arrival times")
    print("   are right, the load fits, the window is met. There is simply no")
    print("   way to get the pallet off the vehicle.")


def show_incompatibility() -> None:
    """FR-10, which was not modelled at all before E-22."""
    print("\n2. Order to order — what may not share a compartment")
    food = an_order("O1", "C1", order_class="FOOD",
                    incompatible_with=frozenset({"HAZMAT"}))
    hazmat = an_order("O2", "C2", order_class="HAZMAT")
    problem = instance((food, hazmat), (a_van(), a_van("V2")))

    judge("both on one van", problem, {"V1": ["O1", "O2"]})
    judge("one each", problem, {"V1": ["O1"], "V2": ["O2"]})

    reversed_ = instance((an_order("O1", "C1", order_class="FOOD"),
                          an_order("O2", "C2", order_class="HAZMAT",
                                   incompatible_with=frozenset({"FOOD"}))),
                         (a_van(),))
    judge("declared from the other side", reversed_, {"V1": ["O1", "O2"]})
    print("   Incompatibility is stated once and holds both ways. Requiring it")
    print("   on both orders would mean a single missing declaration silently")
    print("   permits the pairing.")


def show_access() -> None:
    """FR-11, by class and by weight."""
    print("\n3. Vehicle to site — where the vehicle may go")
    bike_only = (DEPOT, Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1,
                                 access_classes=frozenset({"BIKE"})))
    problem = instance((an_order("O1", "C1"),),
                       (a_van(access_class="RIGID"),), bike_only)
    judge("rigid lorry into a bike-only zone", problem, {"V1": ["O1"]})

    permitted = instance((an_order("O1", "C1"),),
                         (a_van(access_class="BIKE"),), bike_only)
    judge("a bike into the same zone", permitted, {"V1": ["O1"]})

    unrestricted = instance((an_order("O1", "C1"),),
                            (a_van(access_class="RIGID"),))
    judge("a site declaring no restriction", unrestricted, {"V1": ["O1"]})

    bridge = (DEPOT, Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1,
                              max_vehicle_kg=3_500))
    heavy = instance((an_order("O1", "C1"),),
                     (a_van(gross_weight_kg=7_500),), bridge)
    judge("7.5t over a 3.5t weight limit", heavy, {"V1": ["O1"]})
    light = instance((an_order("O1", "C1"),),
                     (a_van(gross_weight_kg=3_400),), bridge)
    judge("3.4t over the same limit", light, {"V1": ["O1"]})

    print("   An empty `access_classes` means unrestricted, not \"admits")
    print("   nothing\". The inverse reading would make every ordinary address")
    print("   unreachable the moment one site declared a restriction.")


def show_preflight() -> None:
    """The same facts, before there is a plan to judge."""
    print("\n4. Told before the solve, not only after it")
    lift = frozenset({"TAIL_LIFT"})
    cases = {
        "no van has the skill": instance(
            (an_order("O1", "C1", required_skills=lift),), (a_van(),)),
        "no van may enter the site": instance(
            (an_order("O1", "C1"),), (a_van(access_class="RIGID"),),
            (DEPOT, Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1,
                             access_classes=frozenset({"BIKE"})))),
        "every van is too heavy": instance(
            (an_order("O1", "C1"),), (a_van(gross_weight_kg=7_500),),
            (DEPOT, Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1,
                             max_vehicle_kg=3_500))),
    }
    for label, problem in cases.items():
        for order_id, finding in preflight(problem).items():
            print(f"   {label:<28} {order_id}  {finding.code}")
            print(f"   {'':<28} {finding.detail}")

    print("   One code, three sentences. NO_ELIGIBLE_VEHICLE covers skills and")
    print("   site access alike, so the detail names whichever filter actually")
    print("   emptied the fleet -- an access failure that read \"requires no")
    print("   skills\" would send someone to hire a tail lift.")
    print("   Pre-flight asks whether *some* vehicle could serve one order,")
    print("   ignoring every other order, so it can say no with certainty.")
    print("   INV-10 asks whether a finished plan honoured the answer. Neither")
    print("   is derived from the other, which is why an unenforced constraint")
    print("   could hide behind a passing pre-flight for eight tasks.")


def main() -> int:
    show_skills()
    show_incompatibility()
    show_access()
    show_preflight()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
