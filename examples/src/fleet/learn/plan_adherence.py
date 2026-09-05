"""When three drivers reverse the same route, the map is wrong.

Demonstrates telematics ingestion and plan adherence, landed for E-61/T-61
(CON-6, §12.4, §11.4):

    vrp.adherence   ingestion, sequence dissimilarity, the aggregations
    vrp.model       the plan the executed route is measured against

CON-6: "Trust the plan only as far as it survives contact with reality. Plan
quality MUST be measured against executed reality (GPS/telematics), not against
the solver's own objective."

§12.4 then says the thing that decides what this metric is for: "Systematic,
repeated deviation is a **model defect**, not driver misbehaviour. Experienced
drivers hold tacit knowledge about roads that are hard to navigate, when traffic
is bad, where parking is findable, and which stops are conveniently served
together."

Four things, in order:

1. **The score**, and why it counts adjacent pairs rather than positions.

2. **One route**, planned against driven, with the cost of each.

3. **The dashboard** §12.4 asks for -- by driver, depot and territory.

4. **How to read it**, which is the opposite of how a compliance report reads.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/learn/plan_adherence.py
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

from vrp.adherence import adherence, aggregate, dissimilarity, ingest
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

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600
PLANNED = ["O1", "O2", "O3", "O4"]


def instance(stops: int = 4) -> Problem:
    """Four real deliveries around the Guadalupe depot.

    Adherence is a measure over what a driver did against what the plan said,
    so the stops want to be places a driver could actually have reordered --
    real addresses at real distances, not four points on a line where every
    reordering costs the same.
    """
    locations, matrix, deliveries, _depot = dataset.road_sites(
        stops, strategy="spread", name="adhere")
    return Problem(
        id="adhere", locations=locations,
        orders=tuple(
            Order(id=f"O{i + 1}", kind="JOB", quantities={"kg": d["units"]},
                  delivery=StopSpec(location_id=f"C{i + 1}",
                                    time_windows=(DAY,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 1000}, shift=DAY,
                          start_location_id="D", end_location_id="D",
                          cost_per_metre=1),),
        matrix=matrix)


def plan(problem: Problem, order_ids: list[str]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    steps = [Step(type="START", location_id="D", arrival=0, start_service=0,
                  departure=0)]
    now, here = 0, index["D"]
    for order_id in order_ids:
        stop = problem.order(order_id).delivery
        there = index[stop.location_id]
        now += problem.matrix.duration(here, there)
        steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                          order_id=order_id, arrival=now, start_service=now,
                          departure=now + 60))
        now, here = now + 60, there
    now += problem.matrix.duration(here, index["D"])
    steps.append(Step(type="END", location_id="D", arrival=now,
                      start_service=now, departure=now))
    return Solution(problem_id=problem.id,
                    routes=(Route(vehicle_id="V1", steps=tuple(steps)),),
                    unassigned=(), objective_breakdown={}, status="FEASIBLE")


def show_the_score() -> None:
    print("\n1. The dissimilarity score (§12.4)")
    print(f"   {'driven as':<28}{'score':>7}")
    for label, driven in (
            ("planned", PLANNED),
            ("two stops swapped", ["O2", "O1", "O3", "O4"]),
            ("last stop first", ["O4", "O1", "O2", "O3"]),
            ("reversed", list(reversed(PLANNED))),
            ("one stop skipped", ["O1", "O2", "O3"])):
        print(f"   {label:<28}{dissimilarity(PLANNED, driven):>7}")
    print("   Note that swapping the first two stops scores *higher* than")
    print("   moving the last stop to the front. That is the measure working:")
    print("   pulling O4 forward leaves O1-O2 and O2-O3 intact, two of the")
    print("   three pairs, while swapping O1 and O2 breaks two of them. What")
    print("   is preserved is the shape of the route, not the position of any")
    print("   one stop.")
    print("   Adjacent pairs, not positions. A driver who runs the whole route")
    print("   one stop later has rearranged nothing, and a positional measure")
    print("   would call that a total deviation. A skipped stop counts against")
    print("   the score outright -- a metric comparing only what both have")
    print("   would score a missed delivery as perfect adherence.")


def show_one_route(problem: Problem) -> None:
    print("\n2. One route, planned against driven")
    planned = plan(problem, PLANNED)
    records = [{"vehicle_id": "V1", "driver_id": "ana", "depot_id": "D",
                "territory": "north",
                "stops": [{"order_id": o, "arrival": 600 * (n + 1)}
                          for n, o in enumerate(["O4", "O3", "O2", "O1"])]}]
    executed = ingest(problem, records)
    row = adherence(problem, planned, executed)[0]

    print(f"   planned:  {PLANNED}   {row.planned_cost:,} m")
    print(f"   driven:   {list(executed[0].sequence)}   {row.realised_cost:,} m")
    print(f"   dissimilarity {row.dissimilarity}, cost delta "
          f"{row.cost_delta:+,} m")
    print("   Both numbers, because they answer different questions: one is how")
    print("   much the driver changed, the other is whether it was worth")
    print("   changing. Costs are recomputed from the matrix, not taken from")
    print("   the solver -- that is CON-6 one layer down.")


def show_the_dashboard(problem: Problem) -> None:
    print("\n3. The dashboard (§12.4: by depot, driver, territory)")
    planned = plan(problem, PLANNED)
    fleet = [
        ("ana", "D1", "north", ["O4", "O3", "O2", "O1"]),
        ("ben", "D1", "north", ["O4", "O3", "O2", "O1"]),
        ("cid", "D1", "north", ["O4", "O3", "O1", "O2"]),
        ("dee", "D1", "south", PLANNED),
        ("eve", "D2", "south", PLANNED),
        ("fay", "D2", "south", ["O1", "O2", "O4", "O3"]),
    ]
    executed = ingest(problem, [
        {"vehicle_id": "V1", "driver_id": driver, "depot_id": depot,
         "territory": territory,
         "stops": [{"order_id": o, "arrival": 600 * (n + 1)}
                   for n, o in enumerate(sequence)]}
        for driver, depot, territory, sequence in fleet])
    rows = adherence(problem, planned, executed)

    for dimension in ("territory", "depot_id", "driver_id"):
        print(f"   by {dimension}:")
        print(f"     {'key':<10}{'routes':>8}{'mean score':>13}"
              f"{'mean cost delta':>18}")
        for key, group in aggregate(rows, by=dimension).items():
            print(f"     {key:<10}{group.routes:>8}"
                  f"{group.mean_dissimilarity:>13}"
                  f"{group.mean_cost_delta:>17,}m")


def show_how_to_read_it() -> None:
    print("\n4. How to read it")
    print("   Three drivers in the north reversed the same route. That is not")
    print("   three people being difficult -- §12.4 is explicit: \"Systematic,")
    print("   repeated deviation is a model defect, not driver misbehaviour.\"")
    print("   The drivers know something the matrix does not: a road that is")
    print("   hard to turn out of, a bay that fills by nine, a pair of stops")
    print("   that are obviously done together.")
    print("   §12.4's own priority order starts with extracting that into an")
    print("   explicit model feature -- a zone, an access rule, a service-time")
    print("   archetype -- \"always preferable, it is explainable and")
    print("   auditable\". Which is T-62 and T-64's subject, and they depend on")
    print("   this because there was nothing to learn from until now.")
    print("   Note what this module does not have: a `compliant` field. The")
    print("   moment one exists somebody builds a leaderboard from it, and the")
    print("   metric stops being a diagnosis and becomes a stick. The route")
    print("   count sits beside every mean for the same reason -- one Tuesday")
    print("   is not a pattern, and a mean without a denominator invites")
    print("   somebody to treat it as one.")


def main() -> int:
    problem = instance()
    show_the_score()
    show_one_route(problem)
    show_the_dashboard(problem)
    show_how_to_read_it()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
