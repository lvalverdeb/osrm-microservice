"""The evaluator and the verifier must agree, having been written apart.

SDD §11.2 calls a discrepancy between the two a P1 defect. That is only a
meaningful claim if something actually compares them, which is what this does:
the evaluator builds a timeline, the verifier recomputes it from scratch, and
any disagreement is a bug in one of them.

This is also the cheapest available approximation of SDD §11.1's L2 property
level — generated instances checked against invariants — pending the real
generator in T-05.
"""

from __future__ import annotations

import random

from vrp.evaluator import ObjectiveWeights, build_timeline, evaluate, route_metrics
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify


def random_problem(rng: random.Random, stops: int = 6) -> Problem:
    size = stops + 1
    locations = [Location(id="D", lat=9.9, lon=-84.0, matrix_index=0)]
    locations += [Location(id=f"S{i}", lat=9.9 + i / 100, lon=-84.0 - i / 100,
                           matrix_index=i) for i in range(1, size)]
    # Asymmetric on purpose: a symmetric matrix hides index-transposition bugs.
    durations = tuple(tuple(0 if i == j else rng.randint(60, 900) for j in range(size))
                      for i in range(size))
    distances = tuple(tuple(0 if i == j else rng.randint(500, 20000) for j in range(size))
                      for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"weight": rng.randint(1, 5)},
              delivery=StopSpec(location_id=f"S{i}",
                                time_windows=(TimeWindow(start=0, end=200000),),
                                service_fixed=rng.choice([0, 60, 300])))
        for i in range(1, size)
    )
    vehicles = (Vehicle(id="V1", capacities={"weight": 500},
                        shift=TimeWindow(start=0, end=200000),
                        start_location_id="D", end_location_id="D"),)
    return Problem(id="P", locations=tuple(locations), orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="m", durations=durations,
                                       distances=distances))


def test_a_timeline_the_evaluator_builds_always_satisfies_the_verifier():
    rng = random.Random(20260825)
    for _ in range(300):
        problem = random_problem(rng, stops=rng.randint(2, 8))
        sequence = [order.id for order in problem.orders]
        rng.shuffle(sequence)
        timeline = build_timeline(problem, "V1", sequence)
        metrics = route_metrics(problem, timeline)
        solution = Solution(
            problem_id=problem.id,
            routes=(Route(vehicle_id="V1", steps=timeline),),
            objective_breakdown={"distance": metrics["distance"],
                                 "driving_seconds": metrics["driving_seconds"]},
        )
        report = verify(problem, solution)
        assert report.ok, [str(v) for v in report.violations]


def test_the_two_recompute_the_same_distance():
    """Independently derived, so equality here is evidence rather than tautology."""
    rng = random.Random(7)
    for _ in range(100):
        problem = random_problem(rng, stops=rng.randint(2, 6))
        sequence = [order.id for order in problem.orders]
        result = evaluate(problem, {"V1": sequence},
                          weights=ObjectiveWeights(per_metre=1, per_second=0))
        solution = Solution(
            problem_id=problem.id,
            routes=(Route(vehicle_id="V1", steps=result.timelines["V1"]),),
            objective_breakdown={"distance": result.breakdown["distance"]},
        )
        assert verify(problem, solution).ok
