"""Twenty days of drivers leaving San Jose Central until last.

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

1. **What the drivers do**, and what the matrix says instead. Six real
   deliveries around the Guadalupe depot, two in each of three districts the
   round actually covers.

2. **The prior learned from it**, with how much they agreed.

3. **Adherence before and after**, which is T-64's definition of done.

4. **The guardrail**, shown working on a prior that produces an illegal plan.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/learn/zone_sequence_prior.py
"""

from __future__ import annotations

import collections
import math
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

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
PER_HUB = 2
HUBS = 3


def a_real_round():
    """Six deliveries around the Guadalupe depot, across three districts.

    Two stops in each of the three nearest districts, because a zone-ordering
    prior has nothing to learn from a round that never leaves one. The
    districts are the dataset's own -- San Jose Central, Desamparados, San
    Rafael -- and the distances are the real ones between real addresses.

    Returns:
        The problem, the zone map (order to district), and the depot record.
    """
    corpus = dataset.load()
    depot = corpus.depots[0]

    def near(delivery):
        return ((delivery["latitude"] - depot["latitude"]) ** 2
                + (delivery["longitude"] - depot["longitude"]) ** 2)

    by_hub = collections.defaultdict(list)
    for delivery in sorted(corpus.deliveries, key=near):
        by_hub[delivery["hub"]].append(delivery)
    hubs = sorted((h for h, v in by_hub.items() if len(v) >= PER_HUB),
                  key=lambda h: near(by_hub[h][0]))[:HUBS]
    chosen = [d for hub in hubs for d in by_hub[hub][:PER_HUB]]

    lat_km = 110.57
    lon_km = 111.32 * math.cos(math.radians(depot["latitude"]))
    points = [(0.0, 0.0)] + [
        ((d["longitude"] - depot["longitude"]) * lon_km,
         (d["latitude"] - depot["latitude"]) * lat_km) for d in chosen]
    grid = tuple(tuple(int(math.dist(a, b) * 1000) for b in points)
                 for a in points)

    locations = (Location(id="D", lat=depot["latitude"], lon=depot["longitude"],
                          matrix_index=0),) + tuple(
        Location(id=f"C{i + 1}", lat=d["latitude"], lon=d["longitude"],
                 matrix_index=i + 1) for i, d in enumerate(chosen))
    orders = tuple(
        Order(id=f"O{i + 1}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i + 1}", time_windows=(DAY,),
                                service_fixed=d["service_minutes"] * 60))
        for i, d in enumerate(chosen))

    problem = Problem(
        id="zones", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 100}, shift=DAY,
                          start_location_id="D", end_location_id="D",
                          cost_per_metre=1),),
        matrix=TravelMatrix(version="z", durations=grid, distances=grid))
    zones = {f"C{i + 1}": d["hub"] for i, d in enumerate(chosen)}
    return problem, zones, depot, hubs


PROBLEM, ZONES, DEPOT, HUBS_IN_ORDER = a_real_round()

# What a distance matrix produces: the nearest district first. What the drivers
# do: that district last, twenty days running.
NAIVE = [order.id for order in PROBLEM.orders]
DRIVEN = NAIVE[PER_HUB * (HUBS - 1):] + NAIVE[PER_HUB:PER_HUB * (HUBS - 1)] \
    + NAIVE[:PER_HUB]


def drove(sequence: list[str]) -> ExecutedRoute:
    return ExecutedRoute(
        vehicle_id="V1", driver_id="ana", depot_id="D",
        territory=HUBS_IN_ORDER[0],
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
        # The order's own service time, not a flat minute. These are real
        # deliveries and they take between eight and twenty minutes each;
        # assuming sixty seconds made the timeline disagree with the model and
        # INV-3 fired on every plan, masking the window violation section 4 is
        # about.
        service = stop.service_fixed
        steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                          order_id=order_id, arrival=now, start_service=now,
                          departure=now + service))
        now, here = now + service, there
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
    print(f"   depot:    {DEPOT['name']}")
    print(f"   planned:  {zones_of(problem, NAIVE)}")
    print(f"   driven:   {zones_of(problem, DRIVEN)}   (20 days running)")
    print(f"   The matrix is not wrong: {HUBS_IN_ORDER[0]} really is")
    print("   nearest the depot. The drivers know something it does not.")
    print("   §12.4 lists what: \"roads that are hard to navigate, when")
    print("   traffic is bad, where parking is findable\" -- a bay you can")
    print("   only get into before the morning fills up, a street that is")
    print("   one-way at school run. None of it is in a distance matrix.")
    print("   §12.4 is explicit about how to read this: \"Systematic, repeated")
    print("   deviation is a model defect, not driver misbehaviour.\"")


def show_the_prior(problem: Problem) -> None:
    print("\n2. The prior learned from it (§12.4 step 2)")
    for label, history in (
            ("20 days, all the same", [drove(DRIVEN)] * 20),
            (f"12 days {HUBS_IN_ORDER[0][:12]}-first, 8 the other way",
             [drove(NAIVE)] * 12 + [drove(DRIVEN)] * 8),
            ("3 days, all different",
             [drove(NAIVE), drove(DRIVEN),
              drove(["O3", "O4", "O5", "O6", "O1", "O2"])]),
            ("no history at all", [])):
        prior = learn_prior(problem, history, ZONES)
        shown = " -> ".join(prior.sequence) if prior.sequence else "(empty)"
        print(f"   {label:<38}{shown:<50}{prior.confidence / 10:>5.0f}%")

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
    # The first stop -- in the district nearest the depot -- gets a receiving
    # bay that shuts ninety minutes into the shift. The prior-ordered round
    # reaches that district at a hundred minutes, so the ordering the drivers
    # taught it is, for this one stop, no longer legal.
    closes = 90 * 60
    first = NAIVE[0]
    tight = replace(problem, orders=tuple(
        replace(order, delivery=replace(order.delivery, time_windows=(
            TimeWindow(start=0, end=closes),)))
        if order.id == first else order for order in problem.orders))

    prior = ZonePrior(sequence=tuple(reversed(HUBS_IN_ORDER)), confidence=1000)
    advised = order_by_prior(tight, NAIVE, prior, ZONES)
    report = verify(tight, plan(tight, advised))

    print(f"   {first} is in {ZONES['C1']}; its bay shuts "
          f"{closes // 60} minutes into the shift")
    print(f"   the prior still leaves that district until last: {advised}")
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
    problem = PROBLEM
    show_the_disagreement(problem)
    show_the_prior(problem)
    show_adherence(problem)
    show_the_guardrail(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
