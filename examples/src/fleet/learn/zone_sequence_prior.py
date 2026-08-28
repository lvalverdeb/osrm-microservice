"""Twenty days of drivers going south first.

Demonstrates the zone-sequence prior landed for E-64/T-64 (§12.4 step 2):

    vrp.zones       learning the ordering, and applying it as a warm start
    vrp.adherence   T-61's dissimilarity, which is what "improves" means here
    vrp.verify      §11.2, downstream of all learning by design

§12.4's "Act" list is in priority order. Step 1 is to extract the deviation into
an explicit model feature, "always preferable -- it is explainable and
auditable". Only "where the pattern resists formalisation" does it reach for
learning: "learn a sequencing prior at the zone level... Zone-sequence learning
from historical routes is the approach that performed best in the Amazon
challenge, where a probabilistic model of zone ordering learned from drivers
outperformed hand-coded zone constraints."

Then the guardrail that decides the whole design: "Learned components MUST be
advisory: they may bias search and warm starts, they MUST NOT be able to produce
a plan that violates a hard constraint. The verifier (§11.2) is downstream of
all learning."

Four things, in order:

1. **What the drivers do**, and what the matrix says instead.

2. **The prior learned from it**, with how much they agreed.

3. **Adherence before and after**, which is T-64's definition of done.

4. **The guardrail**, shown working on a prior that produces an illegal plan.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/learn/zone_sequence_prior.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.adherence import ExecutedRoute, adherence
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
from vrp.zones import ZonePrior, learn_prior, order_by_prior, zone_of

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600
ZONES = {"C1": "north", "C2": "north", "C3": "middle",
         "C4": "middle", "C5": "south", "C6": "south"}
NAIVE = ["O1", "O2", "O3", "O4", "O5", "O6"]
DRIVEN = ["O5", "O6", "O3", "O4", "O1", "O2"]


def instance(stops: int = 6) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="zones",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D",
                          cost_per_metre=1),),
        matrix=TravelMatrix(version="z", durations=grid, distances=grid))


def drove(sequence: list[str]) -> ExecutedRoute:
    return ExecutedRoute(
        vehicle_id="V1", driver_id="ana", depot_id="D", territory="north",
        sequence=tuple(sequence),
        arrivals={o: 600 * (n + 1) for n, o in enumerate(sequence)})


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


def zones_of(problem: Problem, sequence: list[str]) -> str:
    seen = []
    for order_id in sequence:
        zone = zone_of(problem, order_id, ZONES)
        if not seen or seen[-1] != zone:
            seen.append(zone)
    return " -> ".join(seen)


def show_the_disagreement(problem: Problem) -> None:
    print("\n1. What the matrix says, and what the drivers do")
    print(f"   planned:  {zones_of(problem, NAIVE)}")
    print(f"   driven:   {zones_of(problem, DRIVEN)}   (20 days running)")
    print("   The matrix is not wrong about distance -- north really is nearest")
    print("   the depot. The drivers know something it does not: §12.4 lists")
    print("   \"roads that are hard to navigate, when traffic is bad, where")
    print("   parking is findable\". None of that is in a distance matrix.")
    print("   §12.4 is explicit about how to read this: \"Systematic, repeated")
    print("   deviation is a model defect, not driver misbehaviour.\"")


def show_the_prior(problem: Problem) -> None:
    print("\n2. The prior learned from it (§12.4 step 2)")
    for label, history in (
            ("20 days, all the same", [drove(DRIVEN)] * 20),
            ("12 north-first, 8 south-first",
             [drove(NAIVE)] * 12 + [drove(DRIVEN)] * 8),
            ("3 days, all different",
             [drove(NAIVE), drove(DRIVEN),
              drove(["O3", "O4", "O5", "O6", "O1", "O2"])]),
            ("no history at all", [])):
        prior = learn_prior(problem, history, ZONES)
        shown = " -> ".join(prior.sequence) if prior.sequence else "(empty)"
        print(f"   {label:<32}{shown:<28}{prior.confidence / 10:>5.0f}%")

    print("   Drivers disagree, so the majority wins and the confidence says")
    print("   how close it was -- a prior that refused to commit would be no")
    print("   prior at all. No history gives an empty prior rather than a")
    print("   guess: one fitted on nothing that returned an ordering anyway")
    print("   would be indistinguishable from a learned one.")


def show_adherence(problem: Problem) -> None:
    print("\n3. Adherence, before and after (T-64's definition of done)")
    prior = learn_prior(problem, [drove(DRIVEN)] * 20, ZONES)
    advised = order_by_prior(problem, NAIVE, prior, ZONES)

    before = adherence(problem, plan(problem, NAIVE), (drove(DRIVEN),))[0]
    after = adherence(problem, plan(problem, advised), (drove(DRIVEN),))[0]

    print(f"   plan ordered by the matrix: {NAIVE}")
    print(f"     dissimilarity against what was driven: {before.dissimilarity}")
    print(f"   plan ordered by the prior:  {advised}")
    print(f"     dissimilarity against what was driven: {after.dissimilarity}")
    print(f"   the plan still verifies: {verify(problem, plan(problem, advised)).ok}")
    print("   Both halves matter. T-64 pairs \"improves adherence\" with \"no")
    print("   verifier regressions\", and a prior that improved adherence by")
    print("   producing plans the verifier rejects would be worse than none.")


def show_the_guardrail(problem: Problem) -> None:
    print("\n4. The guardrail (§12.4: advisory only)")
    tight = replace(problem, orders=tuple(
        replace(order, delivery=replace(order.delivery, time_windows=(
            TimeWindow(start=0, end=2 * LEG),)))
        if order.id == "O1" else order for order in problem.orders))

    prior = ZonePrior(sequence=("south", "middle", "north"), confidence=1000)
    advised = order_by_prior(tight, NAIVE, prior, ZONES)
    report = verify(tight, plan(tight, advised))

    print(f"   O1's window now closes at {2 * LEG} s")
    print(f"   the prior still puts it last: {advised}")
    print(f"   the plan verifies: {report.ok}")
    for violation in report.violations[:1]:
        print(f"     {violation.invariant}: {violation.detail}")

    print("   The prior was not overruled and it was not asked to be careful.")
    print("   It gave the ordering it learned, the verifier rejected the plan,")
    print("   and that is exactly the arrangement §12.4 requires: \"they may")
    print("   bias search and warm starts, they MUST NOT be able to produce a")
    print("   plan that violates a hard constraint. The verifier is downstream")
    print("   of all learning.\"")
    print("   There is deliberately no way to express this prior as a lock or")
    print("   as a penalty the search cannot overrule. Being unable to break")
    print("   anything is a property of what it returns, not of how carefully")
    print("   it was trained.")


def main() -> int:
    problem = instance()
    show_the_disagreement(problem)
    show_the_prior(problem)
    show_adherence(problem)
    show_the_guardrail(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
