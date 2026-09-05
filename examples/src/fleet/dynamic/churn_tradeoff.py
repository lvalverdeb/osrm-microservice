"""What a quiet afternoon is worth, in money.

Demonstrates the stability term landed for E-57/T-57 (§8.3):

    vrp.stability   the two kinds of churn, their price, and the curve
    vrp.triggers    T-56's re-optimisation, now able to prefer staying put
    vrp.objective   Tier 6, where §8.3 says churn belongs

§8.3: "Re-optimisation MUST be stability-aware: report and optionally penalise
churn (stops moved between vehicles, ETA shifts communicated to customers). A
0.5% cost gain that reshuffles half the plan at 14:00 is a net loss."

T-56 did the reporting. This does the penalising, and then refuses to pick the
weight -- because what churn costs is a fact about a business rather than about
routing. A courier network re-planning every ten minutes and a grocery delivery
with booked slots are not the same problem.

Four things, in order:

1. **Two kinds of churn**, counted apart, because they land on different people.

2. **The curve** T-57 asks for: cost against stability, one point per weight.

3. **Reading it**, which is the point of producing it.

4. **Where no curve exists**, and why that is a correct answer rather than a
   broken one.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/dynamic/churn_tradeoff.py
"""

from __future__ import annotations

import sys
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
from vrp.stability import churn, churn_cost, tradeoff
from vrp.triggers import Trigger, reoptimise

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600
ROUTES = {"V1": [f"O{i}" for i in range(1, 6)],
          "V2": [f"O{i}" for i in range(6, 11)],
          "V3": [f"O{i}" for i in range(11, 16)]}


def instance(stops: int = 15, vans: int = 3) -> Problem:
    """Fifteen real deliveries, whose geography is uneven for free.

    The curve below needs each displaced stop to face a *different* saving
    from moving; on a regular line they all face the same one, so a single
    weight tips every stop at once and the "curve" is a step from six moves to
    zero. That is a fact about the fixture rather than the penalty.

    This used to be arranged by spacing stops with `(i * i) % 17` -- unevenness
    somebody had to invent and defend. Real addresses are uneven because towns
    are, which is both the honest source and one less thing to justify.
    """
    locations, matrix, deliveries, _depot = dataset.road_sites(
        stops, strategy="spread", name="churn")
    return Problem(
        id="churn", locations=locations,
        orders=tuple(
            Order(id=f"O{i + 1}", kind="JOB", quantities={"kg": 1},
                  delivery=StopSpec(location_id=f"C{i + 1}",
                                    time_windows=(DAY,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                               start_location_id="D", end_location_id="D",
                               cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=matrix)

def plan(problem: Problem, assignment: dict[str, list[str]],
         starts: dict[str, int] | None = None) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        clock = (starts or {}).get(vehicle_id, 0)
        steps = [Step(type="START", location_id="D", arrival=clock,
                      start_service=clock, departure=clock)]
        here = index["D"]
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


def show_two_kinds(problem: Problem) -> None:
    print("\n1. Two kinds of churn, counted apart (§8.3)")
    before = plan(problem, ROUTES)

    reassigned = plan(problem, {"V1": ["O1", "O2", "O4", "O5"],
                                "V2": [*ROUTES["V2"], "O3"],
                                "V3": ROUTES["V3"]})
    delayed = plan(problem, ROUTES, starts={"V1": HOUR})

    print(f"   {'change':<34}{'moved':>7}{'ETA drift':>12}")
    for label, after in (("O3 moves from V1 to V2", reassigned),
                         ("V1 leaves an hour later", delayed),
                         ("nothing at all", before)):
        measured = churn(before, after)
        print(f"   {label:<34}{measured.moved:>7}"
              f"{measured.eta_shift // 60:>10} min")

    print("   A stop moving van is a driver's problem: an unplanned route, an")
    print("   address they do not know. A stop keeping its van and shifting an")
    print("   hour is a customer's problem: somebody was told a time and it is")
    print("   now wrong. Summing them would take a real decision away from an")
    print("   operation that prices them differently.")
    print(f"   at 1,000 a move and 2 a second: "
          f"{churn_cost(before, reassigned, 1_000, 2):,} for the reassignment, "
          f"{churn_cost(before, delayed, 1_000, 2):,} for the delay")


def show_the_curve(problem: Problem) -> None:
    print("\n2. The curve (T-57's definition of done)")
    current = plan(problem, ROUTES)
    trigger = Trigger("ETA_DRIFT", 0, vehicle_id="V1")

    curve = tradeoff(problem, current, trigger, now=0, neighbours=2,
                     weights=(0, 200, 500, 1_000, 2_000, 4_000, 8_000))
    print(f"   {'weight':>10}{'stops moved':>14}{'ETA drift':>12}{'cost':>10}")
    for point in curve:
        print(f"   {point.weight:>10,}{point.churn:>14}"
              f"{point.eta_shift // 60:>9} min{point.cost:>10,}")
    print("   Left column is what the operation says a reassignment costs.")
    print("   Everything else follows from it.")
    print("   The curve is not perfectly monotone, and that is worth knowing")
    print("   rather than smoothing away: the re-planner places displaced stops")
    print("   one at a time, so raising the penalty changes which route the")
    print("   first one takes and the rest cascade differently. §8.4 gives this")
    print("   tier thirty seconds, and a re-planner that guaranteed monotonicity")
    print("   would have to search rather than insert. Read the frontier, not")
    print("   the individual steps.")


def show_reading_it(problem: Problem) -> None:
    print("\n3. Reading it")
    current = plan(problem, ROUTES)
    trigger = Trigger("ETA_DRIFT", 0, vehicle_id="V1")
    curve = tradeoff(problem, current, trigger, now=0, neighbours=2,
                     weights=(0, 8_000))
    cheap, steady = curve[0], curve[-1]

    saving = steady.cost - cheap.cost
    print(f"   cheapest plan:  cost {cheap.cost:,}, {cheap.churn} stops moved")
    print(f"   steadiest plan: cost {steady.cost:,}, {steady.churn} stops moved")
    print(f"   the difference: {saving:,} saved for {cheap.churn} reassignments")
    print(f"   which is worth taking if a reassignment costs less than "
          f"{saving // max(cheap.churn, 1):,}")
    print("   §8.3: \"A 0.5% cost gain that reshuffles half the plan at 14:00")
    print("   is a net loss.\" That sentence is a judgement about a business,")
    print("   not about routing, so the curve stops here and the choice is")
    print("   handed over. A hard-coded weight would be this codebase deciding")
    print("   on the operation's behalf.")


def show_where_no_curve_exists(problem: Problem) -> None:
    print("\n4. Where there is no trade to make")
    current = plan(problem, ROUTES)
    trigger = Trigger("BREAKDOWN", 0, vehicle_id="V2")

    free = reoptimise(problem, current, trigger, now=0, churn_weight=0)
    dear = reoptimise(problem, current, trigger, now=0, churn_weight=10 ** 7)
    print(f"   a breakdown, churn priced at 0:          "
          f"{free.delta.churn} stops moved")
    print(f"   the same breakdown, priced at 10,000,000: "
          f"{dear.delta.churn} stops moved")
    print("   A van that has broken cannot keep its work. Every candidate")
    print("   route is a move, so the penalty is a constant added to every")
    print("   option and changes nothing -- the curve is flat, and correctly")
    print("   so. A weight that appeared to buy stability here would be lying")
    print("   about what it could deliver.")
    print("   Worth saying out loud because a flat curve is exactly what a")
    print("   broken weight looks like, and the two need telling apart.")


def main() -> int:
    problem = instance()
    show_two_kinds(problem)
    show_the_curve(problem)
    show_reading_it(problem)
    show_where_no_curve_exists(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
