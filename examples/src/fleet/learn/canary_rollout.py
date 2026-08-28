"""Deciding in advance what would make you stop.

Demonstrates shadow mode and canary rollout, landed for E-65/T-65 (§11.4):

    vrp.rollout     shadow days, divergences, the go/no-go
    vrp.adherence   T-61's metric, which is what "the gap" means here

§11.4 opens with the sentence the whole thing serves: "Benchmarks validate the
algorithm; only production validates the model." Then three stages -- shadow
mode, a one-depot canary with "explicit rollback criteria agreed in advance",
and plan adherence as "the metric that tells you whether the model is right".

Four things, in order:

1. **Shadow mode**: plans produced daily and never executed, measured against
   what actually happened.

2. **Which days to interrogate**, and why not all of them.

3. **The go/no-go**, written down.

4. **Why the criteria are fingerprinted**, which is the only part of "agreed in
   advance" a library can enforce.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/learn/canary_rollout.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.adherence import ExecutedRoute
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
from vrp.rollout import Canary, Criterion, decide, divergences, shadow

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600
ORDER = ["O1", "O2", "O3", "O4"]
REVERSED = list(reversed(ORDER))


def instance(stops: int = 4) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="rollout",
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
        matrix=TravelMatrix(version="r", durations=grid, distances=grid))


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


def drove(sequence: list[str], depot: str = "D1") -> ExecutedRoute:
    return ExecutedRoute(
        vehicle_id="V1", driver_id="ana", depot_id=depot, territory="north",
        sequence=tuple(sequence),
        arrivals={o: 600 * (n + 1) for n, o in enumerate(sequence)})


# Days in between the extremes, so a threshold has something to separate. A
# fixture where every day is either perfect or reversed makes any threshold look
# identical, which reads as a broken threshold rather than a flat fixture.
NUDGED = ["O2", "O1", "O3", "O4"]
SHUFFLED = ["O4", "O1", "O2", "O3"]


def a_month(problem: Problem, bad_days: int = 0):
    """Twenty days at D1, some of which the drivers did their own thing."""
    drift = [drove(REVERSED), drove(SHUFFLED), drove(NUDGED)]
    executed = ([drift[n % len(drift)] for n in range(bad_days)]
                + [drove(ORDER)] * (20 - bad_days))
    return shadow(problem, lambda day: plan(problem, ORDER), executed)


def show_shadow(problem: Problem) -> None:
    print("\n1. Shadow mode (§11.4)")
    days = a_month(problem, bad_days=3)
    print(f"   {len(days)} days planned and never executed")
    print(f"   {'dissimilarity':>15}{'days':>7}")
    counts: dict[int, int] = {}
    for day in days:
        counts[day.dissimilarity] = counts.get(day.dissimilarity, 0) + 1
    for score, count in sorted(counts.items()):
        print(f"   {score:>15}{count:>7}")
    print("   The shadow plan never reaches a vehicle. This takes what actually")
    print("   happened as input and returns a comparison; there is no path out")
    print("   of the module to a dispatch, which is how §11.4's \"without")
    print("   executing them\" is kept -- a property of the shape rather than a")
    print("   promise in a comment.")


def show_divergences(problem: Problem) -> None:
    print("\n2. Which days to interrogate")
    days = a_month(problem, bad_days=3)
    for threshold in (0, 400, 900):
        flagged = divergences(days, threshold=threshold)
        print(f"   threshold {threshold:>4}: {len(flagged)} of {len(days)} days"
              f" flagged")
    print("   §11.4 says \"interrogate every large divergence\", not every")
    print("   divergence. The threshold is what makes the list short enough to")
    print("   read: a low one surfaces the driver who swapped two stops, and")
    print("   §12.4 has already established that routine deviation is")
    print("   information rather than a fault. Where to put it is an")
    print("   operational judgement, which is why it is an argument.")


def show_the_decision(problem: Problem) -> None:
    print("\n3. The go/no-go, written down")
    canary = Canary(depot_id="D1", criteria=(
        Criterion(name="adherence", limit=500),
        Criterion(name="cost_delta", limit=2_000)), minimum_days=20)

    for label, bad in (("a clean month", 0), ("three bad days", 3)):
        decision = decide(canary, a_month(problem, bad_days=bad))
        print(f"   {label}:")
        for line in decision.summary.splitlines():
            print(f"     {line}")

    print("   Any criterion failing is a no-go -- not a score, not a majority.")
    print("   §11.4 calls them rollback criteria, and a criterion that can be")
    print("   outvoted is a preference.")

    print("\n   and the two failure modes that fail closed:")
    for label, days in (("no data arrived", []),
                        ("three days in", a_month(problem)[:3])):
        decision = decide(canary, days)
        print(f"     {label}: {decision.summary.splitlines()[0]}")
    print("   A month where the data never arrived looks exactly like a month")
    print("   where everything went well, unless somebody decided in advance")
    print("   which it is. A rollout tool whose default answer is \"ship it\" is")
    print("   the wrong way round.")


def show_the_fingerprint(problem: Problem) -> None:
    print("\n4. Why the criteria are fingerprinted")
    agreed = Canary(depot_id="D1",
                    criteria=(Criterion(name="adherence", limit=100),),
                    minimum_days=20)
    relaxed = Canary(depot_id="D1",
                     criteria=(Criterion(name="adherence", limit=1_500),),
                     minimum_days=20)
    days = a_month(problem, bad_days=3)

    for label, canary in (("as agreed", agreed), ("bar moved", relaxed)):
        decision = decide(canary, days)
        print(f"   {label:<12}{canary.fingerprint[:8]}  "
              f"{'GO' if decision.go else 'NO-GO'}")

    print("   Same data, same depot, opposite answers. The fingerprint does not")
    print("   prevent that -- nothing in a library can -- but it puts the move")
    print("   in the record: a decision reported against criteria nobody can")
    print("   check were the ones agreed is the failure this is for.")
    print("   The ordinary version is not fraud. The run lands 4% down,")
    print("   somebody remembers that 5% was always the real line, and the")
    print("   canary has demonstrated nothing. §11.4 says \"agreed in advance\"")
    print("   for that reason, and the hash is the only part of it a library")
    print("   can enforce.")
    print("\n   T-65 also asks for \"one depot canary run completed\". That needs")
    print("   a depot and a month. What is here is the tooling and the written")
    print("   decision it produces.")


def main() -> int:
    problem = instance()
    show_shadow(problem)
    show_divergences(problem)
    show_the_decision(problem)
    show_the_fingerprint(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
