"""Portfolio runner with canonical scoring — §7.3, T-36.

§7.3 keeps several engines because different instance shapes suit different
algorithms. That creates a comparison problem, and E-36 names it precisely:
incumbents are "scored by the canonical evaluator, **never the engine's own
accounting**".

This is INV-9's argument one level up. INV-9 exists because a solver's own cost
figure is not evidence about the solver. A portfolio makes it sharper: several
engines each report a number they computed differently -- PyVRP counts one
thing, OR-Tools another, this project's LNS a third -- and comparing those
numbers picks whichever engine is most generous to itself, which is not the same
as picking the best plan.

So the engine's `objective_breakdown` is never read here. Every incumbent is
re-scored from its routes by `vrp.evaluator`, on one scale, and the verifier
gets a veto: CON-1 puts feasibility above optimality, so a cheap illegal plan is
not a better plan but a defect that happens to score well. A portfolio is
exactly where that slips through, because a broken engine's plan can be
arbitrarily cheap.

Win rates by signature are the telemetry that makes §7.3's premise checkable.
"Different engines suit different shapes" is only actionable if somebody records
which won where.

Placement: Python. Orchestration over the adapters, off the request path.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from vrp.evaluator import ObjectiveWeights, evaluate
from vrp.model import Problem, Solution
from vrp.verify import verify

# Size bands for the instance signature. Coarse on purpose: a signature that
# distinguished 41 stops from 42 would give every instance its own bucket and
# no bucket would ever accumulate enough wins to mean anything.
SIZE_BANDS = (10, 50, 200, 1_000)


@dataclass(frozen=True)
class Portfolio:
    """One engine, named. The callable takes a Problem and returns a Solution."""

    name: str
    solve: Callable[[Problem], Solution]


@dataclass
class WinRates:
    """Which engine wins on which instance shape. T-36's telemetry."""

    wins: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def record(self, signature: str, engine: str) -> None:
        self.wins[signature][engine] += 1

    def observations(self, signature: str) -> int:
        return sum(self.wins[signature].values()) if signature in self.wins else 0

    def rate(self, signature: str, engine: str) -> float:
        """Share of runs on this shape that this engine won.

        Zero observations gives zero rather than raising or guessing. It is not
        a claim that the engine loses on this shape -- it has never been
        offered one -- and `observations` is how a caller tells the two apart.
        """
        total = self.observations(signature)
        return self.wins[signature][engine] / total if total else 0.0


@dataclass(frozen=True)
class Outcome:
    """What the portfolio decided, and why."""

    winner: str | None
    best: Solution | None
    scores: dict[str, int]
    rejected: dict[str, str]


def instance_signature(problem: Problem) -> str:
    """A coarse shape label, for grouping win rates. §7.3, T-36.

    Bands rather than exact counts: a signature distinguishing 41 stops from 42
    would give every instance its own bucket, and a bucket with one observation
    tells nobody anything about which engine to prefer.
    """
    orders = len(problem.orders)
    band = next((str(edge) for edge in SIZE_BANDS if orders <= edge), "large")

    windows = any(
        window.end - window.start < 4 * 3600
        for order in problem.orders
        for stop in (order.delivery or order.pickup,)
        for window in stop.time_windows)
    depots = len({vehicle.start_location_id for vehicle in problem.vehicles})
    hours = any(vehicle.hos_rules for vehicle in problem.vehicles)

    return (f"n<={band}|tw={'y' if windows else 'n'}"
            f"|depots={min(depots, 3)}|hos={'y' if hours else 'n'}")


def run_portfolio(problem: Problem, engines: list[Portfolio],
                  weights: ObjectiveWeights | None = None,
                  rates: WinRates | None = None) -> Outcome:
    """Run every engine, score the survivors on one scale, return the best.

    Args:
        problem: the instance, handed unchanged to each engine.
        engines: the portfolio. §7.3 wants at least one HGS member and one
            ruin-and-recreate member.
        weights: the canonical objective's weights. The same for every engine,
            which is the whole point.
        rates: optional telemetry to record the winner against the instance's
            signature.

    Returns:
        The winner's name and plan, every engine's canonical score, and why any
        engine was rejected. `winner` is None when no engine produced a legal
        plan -- returning something regardless would be the portfolio inventing
        one.

    An engine that raises is rejected rather than fatal. §7.3 runs several
    engines precisely so one can fail, and an adapter that declines an instance
    -- as the OR-Tools one does for shipments -- must not take the run down.
    """
    weights = weights or ObjectiveWeights()
    scores: dict[str, int] = {}
    plans: dict[str, Solution] = {}
    rejected: dict[str, str] = {}

    for engine in engines:
        try:
            solution = engine.solve(problem)
        except Exception as failure:
            rejected[engine.name] = f"{type(failure).__name__}: {failure}"
            continue

        report = verify(problem, solution)
        if not report.ok:
            # CON-1: feasibility before optimality. A plan that does not verify
            # is not a cheap option, it is not an option.
            rejected[engine.name] = (
                "failed verification: "
                + "; ".join(str(v) for v in report.violations[:2]))
            continue

        # The engine's own objective_breakdown is deliberately not read. Every
        # plan is re-scored from its routes, so an engine cannot win by being
        # generous to itself.
        assignment = {route.vehicle_id:
                      [step.order_id for step in route.steps if step.order_id]
                      for route in solution.routes}
        scores[engine.name] = evaluate(problem, assignment, weights).total
        plans[engine.name] = solution

    if not scores:
        return Outcome(winner=None, best=None, scores={}, rejected=rejected)

    winner = min(scores, key=lambda name: (scores[name], name))
    if rates is not None:
        rates.record(instance_signature(problem), winner)
    return Outcome(winner=winner, best=plans[winner], scores=scores,
                   rejected=rejected)
