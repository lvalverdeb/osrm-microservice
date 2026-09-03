"""E06 — Does the independent verifier actually catch a broken plan?

CON-1 makes feasibility a gate, and `T-04`'s definition of done claims the
verifier "detects seeded violations in 100% of mutation tests". The claim is
asserted in the backlog and exercised in the suite; no document shows it
happening, per invariant, on a real plan built from real road distances.

This takes a verified plan from the same instance E02 uses, breaks it in six
ways a solver could plausibly break it, and records what the verifier said.
The interesting column is not whether it failed — it is whether the violation
**names the right invariant**, because a checker that fails for the wrong
reason sends somebody to the wrong module.

Writes `results/e06_mutation.json`.
"""

from __future__ import annotations

import argparse
import dataclasses
from typing import Any

from common import client, coord, load_deliveries, post, record, sample
from e02_heuristic_vs_solver import SEED, WEIGHTS, build_problem, sequences_from

from vrp.evaluator import evaluate
from vrp.model import Route, Solution
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

CAPACITY = 20


def baseline(problem, solved) -> Solution:
    """The solver's plan, re-expressed with canonical timelines."""
    score = evaluate(problem, sequences_from(solved), weights=WEIGHTS)
    return Solution(
        problem_id=problem.id,
        routes=tuple(Route(vehicle_id=v, steps=t) for v, t in score.timelines.items()),
        objective_breakdown={"distance": score.breakdown["distance"]},
    )


def _edit_step(solution: Solution, route_at: int, step_at: int, **fields) -> Solution:
    """Return `solution` with one step's fields replaced."""
    routes = list(solution.routes)
    steps = list(routes[route_at].steps)
    steps[step_at] = dataclasses.replace(steps[step_at], **fields)
    routes[route_at] = dataclasses.replace(routes[route_at], steps=tuple(steps))
    return dataclasses.replace(solution, routes=tuple(routes))


def mutations(solution: Solution) -> dict[str, tuple[str, Solution]]:
    """Six plausible corruptions, each with the invariant it ought to trip."""
    first = list(solution.routes)
    dropped = list(first[0].steps)
    del dropped[1]                                   # remove one served order
    drop = dataclasses.replace(
        solution,
        routes=(dataclasses.replace(first[0], steps=tuple(dropped)), *first[1:]))

    duplicated = list(first[1].steps)
    duplicated.insert(1, first[0].steps[1])          # same order on two routes
    dupe = dataclasses.replace(
        solution,
        routes=(first[0], dataclasses.replace(first[1], steps=tuple(duplicated)),
                *first[2:]))

    arrival = first[0].steps[2].arrival
    return {
        "order_dropped": ("INV-1", drop),
        "order_on_two_routes": ("INV-1", dupe),
        "arrival_arithmetic_broken": (
            "INV-4", _edit_step(solution, 0, 2, arrival=arrival + 900)),
        "service_before_arrival": (
            "INV-3", _edit_step(solution, 0, 2, start_service=arrival - 600)),
        "capacity_exceeded": (
            "INV-5", _edit_step(solution, 0, 2,
                                load_after={"stops": CAPACITY + 5})),
        "objective_misreported": (
            "INV-9", dataclasses.replace(
                solution,
                objective_breakdown={
                    "distance": solution.objective_breakdown["distance"] - 25_000})),
    }


def main() -> None:
    """Build a plan, verify it, then break it six ways and record each verdict."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stops", type=int, default=60)
    parser.add_argument("--iterations", type=int, default=2000)
    args = parser.parse_args()

    deliveries, depots, _ = load_deliveries()
    depot = depots[0]
    stops = sample([d for d in deliveries if d.get("gam")], args.stops, SEED)

    with client() as http:
        matrix = post(http, "/matrix", {
            "coordinates": [coord(depot)] + [coord(s) for s in stops],
            "annotations": "duration,distance",
        })

    problem = build_problem(depot, stops, matrix["durations"], matrix["distances"],
                            vehicles=(args.stops + CAPACITY - 1) // CAPACITY,
                            capacity=CAPACITY)
    clean = baseline(problem, solve(problem, iterations=args.iterations, seed=0))
    clean_report = verify(problem, clean)
    print(f"  clean plan: {'PASS' if clean_report.ok else 'FAIL'}")

    rows: list[dict[str, Any]] = []
    for name, (expected, broken) in mutations(clean).items():
        report = verify(problem, broken)
        fired = sorted({v.invariant for v in report.violations})
        rows.append({
            "mutation": name,
            "expected_invariant": expected,
            "detected": not report.ok,
            "invariants_fired": fired,
            "named_the_right_one": expected in fired,
            "first_detail": str(report.violations[0]) if report.violations else None,
        })
        mark = "caught" if expected in fired else ("fired, wrong name"
                                                  if not report.ok else "MISSED")
        print(f"  {name:<28} {mark:<18} {','.join(fired) or '-'}")

    print(record("e06_mutation", {
        "stops": args.stops, "capacity": CAPACITY, "seed": SEED,
        "clean_plan_verified": clean_report.ok,
        "not_applicable": sorted(clean_report.not_applicable),
        "mutations": rows,
        "caught": sum(r["named_the_right_one"] for r in rows),
        "total": len(rows),
    }))


if __name__ == "__main__":
    main()
