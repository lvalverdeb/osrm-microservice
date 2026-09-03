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


class UnsendableEngine(Exception):
    """An engine a worker process could not import, named before the pool runs."""



def _solved(problem: Problem, engines: list[Portfolio], workers: int,
            executor: str = "thread"
            ) -> list[tuple[Portfolio, Solution | None, Exception | None]]:
    """Run the portfolio, returning results in engine order.

    **Threads, so §7.7's "separate cores" holds for some engines and not
    others.** PyVRP and OR-Tools are C++ and release the GIL while they solve,
    and they do scale: four members of a portfolio on a 24-stop instance
    measured 2.97x at four workers. The repository's own LNS is pure Python and
    measured **1.00x** -- four threads taking turns at one interpreter. That is
    a property of the engine rather than of this function, and pretending
    otherwise would have somebody sizing a box on a speed-up that only half the
    portfolio can have. Process-based parallelism is what would fix it, and is
    `T-91`.

    Args:
        problem: the instance, shared unchanged. The domain model is frozen, so
            the engines have nothing mutable between them -- which is the only
            level at which a library can honour NFR-05's isolation, and is why
            a test asserts the problem is unchanged after a parallel run.
        engines: the members to run.
        workers: how many may be in flight at once.

    Returns:
        One `(engine, solution, failure)` per member, in the order given.
        Exactly one of `solution` and `failure` is set.

    A pool of one runs inline rather than through an executor. `CON-4`'s
    reproducible mode is "single-threaded", and a single-worker pool is still a
    worker thread -- close enough for most purposes and not for the one that
    says single-threaded.
    """
    if workers == 1:
        return [_attempt(engine, problem) for engine in engines]

    if executor == "process":
        return _in_processes(problem, engines, workers)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="portfolio") as pool:
        # `map` preserves input order, so the winner is a function of the
        # portfolio rather than of which member happened to finish first.
        return list(pool.map(lambda engine: _attempt(engine, problem), engines))


def _in_processes(problem: Problem, engines: list[Portfolio], workers: int
                  ) -> list[tuple[Portfolio, Solution | None, Exception | None]]:
    """The same run, on separate interpreters. NFR-05, §7.7, T-91.

    Worth it only for an engine that holds the GIL, and measurably so: the
    repository's own LNS went from 1.02x across four threads to 3.38x across
    four processes. PyVRP is already parallel in threads and gains nothing here
    but the spawn cost.

    **The caller needs `if __name__ == "__main__"`.** A spawned worker
    re-imports the program's main module, and a program that starts a solve at
    import time will start one in every worker. No library can fix that; this
    one says so.
    """
    import pickle
    from concurrent.futures import ProcessPoolExecutor

    for engine in engines:
        try:
            pickle.dumps(engine.solve)
        except Exception as failure:
            raise UnsendableEngine(
                f"engine {engine.name!r} cannot be sent to a worker process: "
                f"{type(failure).__name__}: {failure}. A worker imports the "
                "engine by name, so a lambda, a closure or a local function "
                "cannot cross -- move it to module level, or run this "
                "portfolio with executor='thread'") from failure

    with ProcessPoolExecutor(max_workers=workers) as pool:
        # The engines are re-attached here rather than sent back: `Portfolio`
        # travels out and only the result comes home, so `map`'s input order
        # is what pairs them up.
        outcomes = list(pool.map(_attempt_remotely,
                                 [(engine.solve, problem) for engine in engines]))
    return [(engine, solution, failure)
            for engine, (solution, failure) in zip(engines, outcomes)]


def _attempt_remotely(work: tuple) -> tuple[Solution | None, Exception | None]:
    """Run one engine in a worker. Module level, so a worker can import it."""
    solve, problem = work
    try:
        return solve(problem), None
    except Exception as failure:
        return None, failure


def _attempt(engine: Portfolio, problem: Problem
             ) -> tuple[Portfolio, Solution | None, Exception | None]:
    """Run one member, catching what it raises rather than letting it out.

    Caught here rather than around the pool: one engine's failure must not
    cancel the others, and an exception escaping a worker would do exactly that
    to everything still queued.
    """
    try:
        return engine, engine.solve(problem), None
    except Exception as failure:
        return engine, None, failure


def run_portfolio(problem: Problem, engines: list[Portfolio],
                  weights: ObjectiveWeights | None = None,
                  rates: WinRates | None = None,
                  workers: int = 1, executor: str = "thread") -> Outcome:
    """Run every engine, score the survivors on one scale, return the best.

    Args:
        problem: the instance, handed unchanged to each engine.
        engines: the portfolio. §7.3 wants at least one HGS member and one
            ruin-and-recreate member.
        weights: the canonical objective's weights. The same for every engine,
            which is the whole point.
        rates: optional telemetry to record the winner against the instance's
            signature.
        workers: §7.7's bounded intra-run parallelism -- how many portfolio
            members may be in flight at once. One is CON-4's reproducible mode,
            "single-threaded, iteration-limited... used for all regression
            tests", and is the default because a library that parallelised
            unasked would make every existing caller's run non-reproducible.
        executor: "thread" or "process". Threads give separate cores only to
            engines that release the GIL: `T-86` measured 3.00x for PyVRP and
            1.00x for the repository's own pure-Python LNS. Processes give them
            to everything -- 3.38x for that same LNS -- at the cost of two
            constraints named under `UnsendableEngine` and in `T-91`'s row.
            "thread" is the default because it imposes neither.

    Returns:
        The winner's name and plan, every engine's canonical score, and why any
        engine was rejected. `winner` is None when no engine produced a legal
        plan -- returning something regardless would be the portfolio inventing
        one.

    Raises:
        ValueError: on a worker count below one, or an unknown executor. Nought
            workers is not "no parallelism": it is a pool that runs nothing,
            and clamping it silently would report a run that solved no engines
            as a run whose engines all declined.
        UnsendableEngine: in process mode, when an engine cannot be pickled --
            a lambda or a closure. Checked before the pool starts so the error
            names the member, rather than arriving as `BrokenProcessPool` from
            three frames inside the standard library.

    An engine that raises is rejected rather than fatal. §7.3 runs several
    engines precisely so one can fail, and an adapter that declines an instance
    -- as the OR-Tools one does for shipments -- must not take the run down.

    **Timing does not decide anything.** Every plan is scored afterwards on the
    canonical objective, and the winner is a `min` over a name-keyed dict with a
    name tiebreak -- so which engine finished first could not change it even if
    results arrived in that order. What completion order *would* change is the
    report: `scores` and `rejected` would list their engines differently on
    every run, and two runs agreeing about every number would serialise to
    different bytes. Results are therefore collected in engine order, which
    makes the whole outcome a function of the portfolio rather than the weather.
    """
    if workers < 1:
        raise ValueError(f"workers must be at least 1; got {workers}")
    if executor not in ("thread", "process"):
        raise ValueError(
            f"unknown executor {executor!r}; use 'thread' or 'process'")
    weights = weights or ObjectiveWeights()
    scores: dict[str, int] = {}
    plans: dict[str, Solution] = {}
    rejected: dict[str, str] = {}

    for engine, solution, failure in _solved(problem, engines, workers,
                                            executor):
        if failure is not None:
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
