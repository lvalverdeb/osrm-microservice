"""The cargo bikes cannot leave before the lorry arrives.

Demonstrates route synchronisation landed for E-76/T-76 (FR-26, INV-15, DEC-1):

    vrp.model        `Synchronisation`, on the problem rather than an order
    vrp.verify       INV-15, the first invariant whose subject is a pair
    vrp.synchronise  the loop that tries to produce a plan it will accept

FR-26: "Support **route synchronisation**: constraints coupling two routes at a
place and time -- a satellite transfer in a two-echelon network, vehicles
departing as a convoy, a trailer meeting a hub cut-off."

`UC-131` names what makes it different in kind: "the second-echelon departure
depends on the first echelon's arrival, which is a synchronisation constraint
**across two routing problems**." Every other constraint here belongs to
something -- a capacity to a vehicle, a window to a stop. This one belongs to a
pair, and both halves can be individually perfect while the plan is fiction.

Three things, in order:

1. **What one pass produces.** Nothing in the search relates two routes'
   timelines, so the bike leaves whenever suits it and both routes verify
   individually.

2. **What the loop does about it.** Solve, see when the lorry actually
   finished, tell the bike it may not start before that, solve again. The lever
   is a time window, which is why it converges for a transfer: the first half
   is not constrained by the second, so it does not move in response.

3. **What the loop cannot do.** Keeping the two halves on *different* vehicles
   is an order-to-order constraint, the same shape as the class incompatibility
   the adapters refuse, and no window expresses it -- one van collecting where
   it just delivered is simply a good route. The fix belongs to the instance: a
   satellite has a receiving bay and a dispatch bay, and an HGV cannot get into
   the second.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/synchronisation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    Synchronisation,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.synchronise import solve_synchronised, unmet
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)
LEG = ((0, 600, 600, 1200), (600, 0, 0, 600),
       (600, 0, 0, 600), (1200, 600, 600, 0))


def hub(distinguishable: bool) -> Problem:
    inbound = frozenset({"HGV"}) if distinguishable else frozenset()
    outbound = frozenset({"BIKE"}) if distinguishable else frozenset()
    locations = (
        Location(id="D", lat=9.90, lon=-84.0, matrix_index=0),
        Location(id="IN", lat=9.95, lon=-84.0, matrix_index=1,
                 access_classes=inbound),
        Location(id="OUT", lat=9.95, lon=-84.0, matrix_index=2,
                 access_classes=outbound),
        Location(id="C", lat=10.0, lon=-84.0, matrix_index=3,
                 access_classes=outbound))
    orders = (Order(id="TRUNK", kind="JOB", quantities={"kg": 50},
                    delivery=StopSpec(location_id="IN", time_windows=(DAY,),
                                      service_fixed=600)),
              Order(id="ONWARD", kind="JOB", quantities={"kg": 50},
                    pickup=StopSpec(location_id="OUT", time_windows=(DAY,),
                                    service_fixed=600)))
    fleet = (Vehicle(id="LORRY", capacities={"kg": 100}, shift=DAY,
                     start_location_id="D", end_location_id="D",
                     access_class="HGV" if distinguishable else None),
             Vehicle(id="BIKE", capacities={"kg": 100}, shift=DAY,
                     start_location_id="D", end_location_id="D",
                     access_class="BIKE" if distinguishable else None))
    return Problem(
        id="echelon", locations=locations, orders=orders, vehicles=fleet,
        matrix=TravelMatrix(version="e", durations=LEG, distances=LEG),
        synchronisations=(Synchronisation(kind="TRANSFER", first="TRUNK",
                                          second="ONWARD", min_gap=300),))


def engine(problem):
    return solve(problem, iterations=400, seed=0)


def timings(solution) -> dict[str, tuple[str, int, int]]:
    return {step.order_id: (route.vehicle_id, step.start_service, step.departure)
            for route in solution.routes for step in route.steps
            if step.order_id}


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def show(label: str, problem, solution) -> None:
    rows = timings(solution)
    print(f"\n   {label}")
    for order_id in ("TRUNK", "ONWARD"):
        if order_id in rows:
            vehicle, start, departure = rows[order_id]
            print(f"      {vehicle:6s} {order_id:7s} "
                  f"start {start:6d}  depart {departure:6d}")
    broken = unmet(problem, solution)
    print(f"      coupling: "
          f"{'unmet -- ' + broken[0].kind if broken else 'met'}"
          f"   verifier: "
          f"{'clean' if verify(problem, solution).ok else 'reports INV-15'}")


def one_pass() -> None:
    heading("1.", "What one pass produces")
    problem = hub(distinguishable=True)
    show("a plain solve:", problem, engine(problem))
    print("\n   Both routes are individually beyond reproach. The load is on a")
    print("   bike that left before the lorry carrying it turned up.")


def the_loop() -> None:
    heading("2.", "What the loop does about it")
    problem = hub(distinguishable=True)
    solution, planned = solve_synchronised(problem, engine)
    show("after the loop:", planned, solution)
    rows = timings(solution)
    print(f"\n   handover: the lorry finishes at {rows['TRUNK'][2]}, the bike")
    print(f"   starts at {rows['ONWARD'][1]} -- "
          f"{rows['ONWARD'][1] - rows['TRUNK'][2]}s later, against a required "
          "300.")
    print("\n   The clock starts when the lorry *finishes*, not when it")
    print("   arrives: what the bike waits for is the load being off.")


def what_it_cannot_do() -> None:
    heading("3.", "What the loop cannot do")
    problem = hub(distinguishable=False)
    try:
        solve_synchronised(problem, engine)
    except NotImplementedError as refusal:
        print("\n   both bays admit both vehicles:\n")
        text = str(refusal)
        while text:
            print(f"      {text[:64]}")
            text = text[64:]
    print("\n   Keeping the two halves apart is an order-to-order constraint,")
    print("   which the adapters already refuse for class incompatibility. A")
    print("   receiving bay an HGV can reach and a dispatch bay it cannot is")
    print("   both realistic and expressible, so the instance says it.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-26, from CAT-VRP-003 §12.2 -- four operations asked for it.")
    one_pass()
    the_loop()
    what_it_cannot_do()
    print(f"\n{'=' * 72}")
    print("INV-15 is the first invariant here whose subject is a pair of")
    print("routes. Everything else in the verifier reads one route and decides;")
    print("this one cannot, which is exactly why the constraint needed writing")
    print("down rather than being left to each route's own good behaviour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
