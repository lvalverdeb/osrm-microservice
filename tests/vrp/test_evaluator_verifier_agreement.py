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
from itertools import pairwise

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


# --------------------------------------------------------------------------
# INV-9 on a plan the system actually produces
# --------------------------------------------------------------------------
# SDD 4.3 calls INV-9 "the single most valuable test in the system": the
# `objective_breakdown` recomputed from `routes` must equal the solver-reported
# objective. `verifier._check_objective` honours that faithfully -- and returns
# at once when the breakdown is empty, which is what every shipped adapter
# reported. So on every plan PyVRP or OR-Tools produced, the most valuable test
# in the system checked nothing.
#
# The number reported has to be the *solver's own*, not one recomputed from the
# matrix, or the check compares the verifier's arithmetic with a copy of itself
# and cannot fail. PyVRP's `Solution.distance()` is its own accounting over its
# own compiled model, which is what makes the comparison worth making.


def solved_round():
    """A small instance solved through the real adapter."""
    from vrp.solve.pyvrp_adapter import solve as pyvrp_solve

    rng = random.Random(11)
    problem = random_problem(rng, stops=4)
    return problem, pyvrp_solve(problem, iterations=200, seed=0)


def test_a_solved_plan_reports_an_objective_the_verifier_can_check():
    """Without this, `_check_objective` returns at its first line, every time."""
    _problem, solution = solved_round()
    assert solution.objective_breakdown, (
        "the adapter reported no objective, so INV-9 checks nothing")
    assert "distance" in solution.objective_breakdown


def test_inv9_catches_a_solver_that_misreports_its_distance():
    """The drift INV-9 exists to catch, on a plan the solver actually produced."""
    from dataclasses import replace

    problem, solution = solved_round()
    assert verify(problem, solution).ok

    drifted = replace(solution, objective_breakdown=dict(
        solution.objective_breakdown,
        distance=solution.objective_breakdown["distance"] + 1))
    report = verify(problem, drifted)
    assert not report.ok
    assert any("INV-9" in str(v) for v in report.violations), report.violations


def test_the_reported_distance_is_the_solver_s_own_arithmetic():
    """Not a recomputation from the matrix, which would be a tautology.

    PyVRP sums distance over its compiled model; the verifier sums it over
    `problem.matrix` from the mapped steps. Equality is evidence that the
    compile-and-map round trip preserved the arcs, which is exactly the class
    of bug INV-9 is for.
    """
    problem, solution = solved_round()
    walked = 0
    for route in solution.routes:
        for before, after in pairwise(route.steps):
            walked += problem.matrix.distance(
                problem.location(before.location_id).matrix_index,
                problem.location(after.location_id).matrix_index)
    assert solution.objective_breakdown["distance"] == walked
