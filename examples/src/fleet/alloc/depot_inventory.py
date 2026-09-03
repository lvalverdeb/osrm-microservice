"""A depot is not a spring.

Demonstrates depot inventory and depot choice, landed for E-45/T-45 (FR-31,
DEC-1, §7.8):

    vrp.model      `Location.inventory`, §4.1's `inventory_by_dimension`
    vrp.verify     INV-13, counted globally rather than per route
    vrp.diagnose   `DEPOT_STOCKOUT`, which had no subject until now

FR-31: "Where multiple depots can serve an order, choose the depot as part of
optimisation, subject to inventory availability per depot."

Multi-depot fleets have worked since E-21 -- a vehicle carries its start
location, so choosing the van chooses the depot. What was missing is the clause
after the comma. A plan that loads thirty tonnes out of a depot holding
twenty-four is not expensive or late. It cannot happen, and until T-45 every
invariant in the system passed it.

Four things, in order:

1. **The plan that could not happen.** Two vans, one depot, and a draw the
   depot cannot meet.

2. **Why it has to be global.** Each van's route is individually beyond
   reproach. The failure exists only in the sum -- which is exactly the shape
   §7.6's decomposition produces, and exactly what DEC-1 forbids.

3. **Per depot and per dimension.** Two depots are not one pooled depot, and a
   depot full of pallets and out of chilled space is out of chilled space.

4. **Told before the solve.** `DEPOT_STOCKOUT` has been declared unimplemented
   since E-14 with the honest reason "depot inventory is not modelled". It is
   modelled now.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/alloc/depot_inventory.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

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
    Vehicle,
)
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DAY = TimeWindow(start=0, end=12 * 3600)
LEG = 600


_CORPUS = None


def corpus():
    """The delivery corpus, read once for every instance below."""
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = dataset.load()
    return _CORPUS


def instance(depots: tuple[Location, ...], vehicles: tuple[Vehicle, ...],
             stops: int = 2, kg: int = 10, pallets: int = 0) -> Problem:
    """One inventory scenario, on real depots and real road travel.

    Distance is not what this example is about -- INV-13 counts stock against
    what left each depot, and that arithmetic is the same whatever the legs
    are. What real geography changes is the setting: the depots are the six
    the corpus actually ships from, and a stockout at one of them is a
    scenario a dispatcher recognises rather than two labels on the same point.

    Args:
        depots: Depot locations, already carrying their inventory.
        vehicles: The fleet, homed on those depots.
        stops: How many deliveries the scenario serves.
        kg: Weight per drop -- the controlled variable, held per scenario.
        pallets: Second dimension per drop, when the scenario uses one.

    Returns:
        A `Problem` over real coordinates and real road travel.
    """
    deliveries, _ = corpus().spread(stops, depot=corpus().depots[0])
    customers = tuple(
        Location(id=f"C{i + 1}", lat=d["latitude"], lon=d["longitude"],
                 matrix_index=len(depots) + i)
        for i, d in enumerate(deliveries))
    locations = (*depots, *customers)
    matrix, road = dataset.road_matrix_or_planar(
        [(loc.lat, loc.lon) for loc in locations], GATEWAY, "depot-inv")
    if not road:
        print("   no gateway; distances are straight-line, so the costs"
              " below are lower than the road gives")

    quantities = {"kg": kg} | ({"pallets": pallets} if pallets else {})
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities=dict(quantities),
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, stops + 1))
    return Problem(id="inv", locations=locations, orders=orders,
                   vehicles=vehicles, matrix=matrix)


def depot(depot_id: str, index: int, **kwargs) -> Location:
    """A real depot from the corpus, addressed by position."""
    record = corpus().depots[index]
    return Location(id=depot_id, lat=record["latitude"],
                    lon=record["longitude"], matrix_index=index,
                    **kwargs)


def a_van(vehicle_id: str, home: str, **kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 500, "pallets": 50}, "shift": DAY}
    return Vehicle(id=vehicle_id, start_location_id=home, end_location_id=home,
                   **{**defaults, **kwargs})


def plan(problem: Problem, assignment: dict[str, list[str]]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        home = problem.vehicle(vehicle_id).start_location_id
        steps = [Step(type="START", location_id=home, arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index[home]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            clock += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += problem.matrix.duration(here, index[home])
        steps.append(Step(type="END", location_id=home, arrival=clock,
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


def show_the_impossible_plan() -> None:
    print("\n1. Twenty units out of a depot holding fifteen")
    stocked = instance((depot("D", 0, inventory={"kg": 15}),),
                       (a_van("V1", "D"),), stops=2, kg=10)
    judge("one van, two 10 kg drops, 15 kg in stock", stocked, {"V1": ["O1", "O2"]})

    enough = instance((depot("D", 0, inventory={"kg": 20}),),
                      (a_van("V1", "D"),), stops=2, kg=10)
    judge("the same plan with 20 kg in stock", enough, {"V1": ["O1", "O2"]})

    unmeasured = instance((depot("D", 0),), (a_van("V1", "D"),),
                          stops=2, kg=10)
    judge("a depot that declares no stock at all", unmeasured,
          {"V1": ["O1", "O2"]})
    print("   Unset means unlimited, the reading `dock_capacity` already has.")
    print("   Treating an unmeasured depot as empty would make every existing")
    print("   plan fiction the moment one depot started counting.")


def show_why_global() -> None:
    print("\n2. Each route is fine; the plan is not")
    problem = instance((depot("D", 0, inventory={"kg": 15}),),
                       (a_van("V1", "D"), a_van("V2", "D")), stops=2, kg=10)

    judge("V1 alone: 10 kg of 15", problem, {"V1": ["O1"]})
    judge("V2 alone: 10 kg of 15", problem, {"V2": ["O2"]})
    judge("both, as one plan", problem, {"V1": ["O1"], "V2": ["O2"]})

    print("   No per-route check can see this. The two routes are each within")
    print("   the depot's stock and together they are not, which is precisely")
    print("   the shape §7.6's decomposition produces: sub-problems solved")
    print("   apart, each locally feasible, concatenating to a plan that cannot")
    print("   happen. DEC-1 says it in one line -- depot inventory \"MUST be")
    print("   enforced globally, never per cluster\".")


def show_per_depot_and_dimension() -> None:
    print("\n3. Two depots are not one pooled depot")
    depots = (depot("D1", 0, inventory={"kg": 15}),
              depot("D2", 1, inventory={"kg": 15}))
    problem = instance(depots, (a_van("V1", "D1"), a_van("V2", "D2")),
                       stops=2, kg=10)

    judge("one drop from each depot", problem, {"V1": ["O1"], "V2": ["O2"]})
    judge("both drops from D1", problem, {"V1": ["O1", "O2"]})
    print("   Thirty units of stock and twenty units of work, and the split")
    print("   decides it. Summing across depots would accept the second plan,")
    print("   which asks one depot for more than it holds while the other sits")
    print("   full -- FR-31's \"per depot\" doing real work.")

    print("\n   ...and a depot is short of whatever it is short of")
    mixed = instance((depot("D", 0, inventory={"kg": 100, "pallets": 1}),),
                     (a_van("V1", "D"),), stops=2, kg=10, pallets=1)
    judge("plenty of kilograms, one pallet in stock", mixed,
          {"V1": ["O1", "O2"]})
    print("   A depot full of pallets and out of chilled space is out of")
    print("   chilled space, and the binding dimension is rarely the one")
    print("   somebody thought to check.")


def show_preflight() -> None:
    print("\n4. Told before the solve")
    short = instance((depot("D", 0, inventory={"kg": 5}),),
                     (a_van("V1", "D"),), stops=1, kg=10)
    for order_id, finding in preflight(short).items():
        print(f"   {order_id}  {finding.code}")
        print(f"       {finding.detail}")

    depots = (depot("D1", 0, inventory={"kg": 5}),
              depot("D2", 1, inventory={"kg": 50}))
    supplied = instance(depots, (a_van("V1", "D1"), a_van("V2", "D2")),
                        stops=1, kg=10)
    print(f"   with a second depot holding 50 kg: "
          f"{preflight(supplied) or 'no findings'}")

    print("   Pre-flight asks whether *some* depot could supply one order,")
    print("   ignoring every other order -- so a depot that is short does not")
    print("   condemn work another depot can take. Whether the depots can")
    print("   supply the whole day between them is INV-13's question, and it")
    print("   needs the plan.")
    print("   DEPOT_STOCKOUT was declared unimplemented from E-14 to T-45 with")
    print("   the reason \"depot inventory is not modelled\". A code that stayed")
    print("   on that list after it started being emitted would tell callers to")
    print("   keep waiting for something that had already arrived.")


def main() -> int:
    show_the_impossible_plan()
    show_why_global()
    show_per_depot_and_dimension()
    show_preflight()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
