"""Two solvers, one scoreboard: why a portfolio must not believe its engines.

Demonstrates the portfolio runner landed for E-36/T-36 (§7.3) against the
vendored benchmark instances:

    vrp.portfolio     run every engine, score the survivors on one scale
    vrp.evaluator     the canonical scale
    vrp.verify        the veto -- CON-1 puts feasibility above optimality

§7.3 keeps several engines because different instance shapes suit different
algorithms. That is a reasonable claim, and it creates an unreasonable problem:
once two engines have each returned a plan and a cost, somebody has to choose,
and the two costs were computed by different code. PyVRP counts one thing,
OR-Tools another. Comparing those numbers directly does not pick the better
plan -- it picks the engine that is most generous to itself.

Three things this shows, in order:

1. **The engines disagree about themselves.** Each is asked for a plan on the
   same instance, and each reports a cost. The runner throws both numbers away
   and re-scores the routes. The re-scored figures are the ones on the board.

2. **An engine cannot win by flattering itself.** A third entrant is added: an
   under-searched plan carrying a self-reported cost of 1. On a runner that read
   `objective_breakdown` it would win every instance. On RC208 it loses, and the
   gap between what it claimed and what it scored is printed. On E-n22-k4 it
   *does* come out on top -- because a single iteration already reaches that
   instance's published optimum of 375, every engine ties there, and CON-4 wants
   ties broken deterministically. The run says which of the two happened rather
   than leaving the WINNER column to imply the wrong one.

3. **A cheap illegal plan is not a cheap plan.** A fourth entrant returns
   something the verifier rejects. It is not ranked badly; it is not ranked.

Win rates are recorded by instance signature, which is the point of keeping a
portfolio at all: "different engines suit different shapes" is only actionable
if somebody writes down which won where.

Runs offline against `benchmarks/instances/` -- no gateway and no engine
required, because the argument is about arithmetic, not roads.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/engine_portfolio.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.benchmarks import read_benchmark
from vrp.evaluator import ObjectiveWeights
from vrp.model import Solution
from vrp.portfolio import (
    Portfolio,
    WinRates,
    instance_signature,
    run_portfolio,
)

INSTANCES = PROJECT_ROOT / "benchmarks" / "instances"
WEIGHTS = ObjectiveWeights(per_metre=1, per_second=0)


def flattered(solution: Solution) -> Solution:
    """A legal but under-searched plan, claiming a cost of 1.

    Produced by running the real engine for a single iteration, so it verifies
    like any other plan and is simply worse. Reversing the reference's routes
    was the first attempt and demonstrates nothing: on a symmetric matrix a
    reversed route costs exactly the same, so the "poor" plan tied with the good
    one and won on the alphabetical tie-break -- looking, misleadingly, like the
    runner had believed its self-reported cost.
    """
    return Solution(problem_id=solution.problem_id, routes=solution.routes,
                    unassigned=solution.unassigned, status=solution.status,
                    objective_breakdown={"total": 1})


def broken_plan(solution: Solution) -> Solution:
    """A plan with every timestamp at zero: arbitrarily cheap, and impossible."""
    from vrp.model import Route, Step

    routes = tuple(
        Route(vehicle_id=route.vehicle_id,
              steps=tuple(Step(type=s.type, location_id=s.location_id,
                               order_id=s.order_id, arrival=0, start_service=0,
                               departure=0, load_after=s.load_after)
                          for s in route.steps))
        for route in solution.routes)
    return Solution(problem_id=solution.problem_id, routes=routes,
                    unassigned=solution.unassigned, status="FEASIBLE",
                    objective_breakdown={"total": 0})


def engines_for(problem) -> tuple[list[Portfolio], dict[str, int]]:
    """The portfolio, plus what each engine said about itself.

    The self-reported figures are collected here purely so the example can
    print them beside the canonical ones. The runner never sees them.
    """
    from vrp.solve import ortools_adapter, pyvrp_adapter

    claimed: dict[str, int] = {}

    def wrap(name: str, solve):
        def run(p):
            solution = solve(p)
            claimed[name] = solution.objective_breakdown.get("total", 0)
            return solution
        return Portfolio(name, run)

    reference = pyvrp_adapter.solve(problem, iterations=200, seed=0)
    weak = pyvrp_adapter.solve(problem, iterations=1, seed=11)
    claimed["flatterer"] = 1
    claimed["broken"] = 0

    return [
        wrap("pyvrp", lambda p: pyvrp_adapter.solve(p, iterations=200, seed=0)),
        wrap("ortools", lambda p: ortools_adapter.solve(p, solutions=100, seed=0)),
        Portfolio("flatterer", lambda _p: flattered(weak)),
        Portfolio("broken", lambda _p: broken_plan(reference)),
    ], claimed


def report(label: str, problem, rates: WinRates) -> None:
    engines, claimed = engines_for(problem)
    result = run_portfolio(problem, engines, weights=WEIGHTS, rates=rates)

    print(f"\n{label}  ({len(problem.orders)} orders)")
    print(f"  signature: {instance_signature(problem)}")
    print(f"  {'engine':<12}{'claimed':>14}{'scored':>14}   verdict")
    for name in ("pyvrp", "ortools", "flatterer", "broken"):
        if name in result.rejected:
            print(f"  {name:<12}{claimed.get(name, 0):>14}{'--':>14}   "
                  f"rejected: {result.rejected[name][:52]}")
            continue
        mark = "WINNER" if name == result.winner else ""
        print(f"  {name:<12}{claimed.get(name, 0):>14}"
              f"{result.scores[name]:>14}   {mark}")

    claim = result.scores.get("flatterer")
    best = min(result.scores.values())
    if claim is None:
        return
    if claim > best:
        print(f"  the flatterer claimed 1 and scored {claim}; it did not win")
    elif result.winner == "flatterer":
        # Not a failure, and worth saying plainly rather than letting the
        # WINNER column imply one. The instance is small enough that a single
        # iteration already reaches its published optimum, so every engine
        # scores the same and the tie falls to the name -- CON-4 wants that
        # tie broken deterministically, and alphabetically is how.
        print(f"  every engine scored {best}; the flatterer won a tie on name, "
              "not on its claim")
    else:
        print(f"  the flatterer tied at {best} and lost the tie-break")


def main() -> int:
    if not INSTANCES.exists():
        print(f"benchmark instances not found at {INSTANCES}")
        return 1

    rates = WinRates()
    for filename, label in (("E-n22-k4.txt", "E-n22-k4"), ("RC208.vrp", "RC208")):
        path = INSTANCES / filename
        if not path.exists():
            continue
        report(label, read_benchmark(path).problem, rates)

    print("\nwin rates by instance signature")
    for signature, wins in rates.wins.items():
        entries = ", ".join(f"{engine} {rates.rate(signature, engine):.0%}"
                            for engine in sorted(wins))
        print(f"  {signature}\n    {entries} "
              f"({rates.observations(signature)} run(s))")
    print("\nThe portfolio is tunable because these were written down. Keeping "
          "several\nengines is only worth the cost if somebody records which "
          "won where.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
