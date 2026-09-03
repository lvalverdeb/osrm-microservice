"""Bounded intra-run parallelism — NFR-05, §7.7, T-86.

`NFR-05`: "Independent planning runs are isolated and parallelisable; a single
run may use bounded intra-run parallelism (§7.7)."

§7.7: "Intra-run parallelism: portfolio members on separate cores... Shared
state is limited to the incumbent pool behind a lock-free exchange. Reproducible
mode (CON-4) forces single-threaded, iteration-limited execution and is used for
all regression tests."

**"Same winner in parallel and in series" is the trap in this requirement.** It
is satisfied perfectly by a `workers` argument that is accepted and ignored, and
by a thread pool that happens to run everything sequentially. So the tests that
matter here are the ones that fail unless two engines are genuinely in flight at
the same moment: a barrier that no serial execution can pass, and a ceiling that
no unbounded pool can respect.
"""

from __future__ import annotations

import threading
import time

import pytest

from vrp.bench import fixtures
from vrp.model import Problem, Solution
from vrp.portfolio import Portfolio, run_portfolio
from vrp.solve.pyvrp_adapter import solve as pyvrp_solve

TIMEOUT = 5.0


def engine(name: str, body) -> Portfolio:
    return Portfolio(name=name, solve=body)


def a_problem() -> Problem:
    return fixtures.uc075_delivery_station_sequencing()


def real_engines(count: int) -> list[Portfolio]:
    """The same engine several times over: different names, same work.

    Enough to occupy a pool, and every member returns a plan the verifier
    accepts, so scoring is exercised rather than skipped.
    """
    return [engine(f"pyvrp-{index}", pyvrp_solve) for index in range(count)]


# --------------------------------------------------------------------------
# Concurrency that can be proven rather than assumed
# --------------------------------------------------------------------------

def test_two_engines_are_in_flight_at_the_same_moment():
    """The test a `workers` argument that is accepted and ignored cannot pass.

    Both engines wait on a barrier that only releases when both have arrived.
    Run in series the first would wait alone until the timeout, and the run
    would fail; passing means they were genuinely concurrent.
    """
    barrier = threading.Barrier(2, timeout=TIMEOUT)
    problem = a_problem()

    def meet(_: Problem) -> Solution:
        barrier.wait()
        return pyvrp_solve(problem)

    outcome = run_portfolio(problem, [engine("a", meet), engine("b", meet)],
                            workers=2)

    assert not outcome.rejected, outcome.rejected
    assert outcome.winner is not None


def test_one_worker_really_is_one_worker():
    """CON-4's reproducible mode "forces single-threaded". The same barrier,
    with a pool of one, must *not* pass -- and the engine that times out is
    rejected rather than taking the run down."""
    barrier = threading.Barrier(2, timeout=0.5)
    problem = a_problem()

    def meet(_: Problem) -> Solution:
        barrier.wait()
        return pyvrp_solve(problem)

    outcome = run_portfolio(problem, [engine("a", meet), engine("b", meet)],
                            workers=1)

    assert outcome.winner is None
    assert set(outcome.rejected) == {"a", "b"}, (
        "a pool of one let two engines meet at a barrier, so it is not "
        "single-threaded")


def test_the_pool_is_bounded_by_the_worker_count():
    """§7.7 says *bounded*. An unbounded pool on a 200-engine portfolio is a
    fork bomb with a scoring function attached."""
    problem = a_problem()
    live = 0
    peak = 0
    guard = threading.Lock()

    def watched(_: Problem) -> Solution:
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        try:
            return pyvrp_solve(problem)
        finally:
            with guard:
                live -= 1

    run_portfolio(problem, [engine(f"e{i}", watched) for i in range(6)],
                  workers=2)

    assert peak > 1, "nothing ran concurrently, so the bound is untested"
    assert peak <= 2, f"{peak} engines ran at once against a bound of 2"


# --------------------------------------------------------------------------
# The answer does not depend on the timing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("workers", [1, 2, 4])
def test_the_same_portfolio_returns_the_same_winner_at_any_width(workers):
    problem = a_problem()
    outcome = run_portfolio(problem, real_engines(3), workers=workers)
    serial = run_portfolio(problem, real_engines(3), workers=1)

    assert outcome.winner == serial.winner
    assert outcome.scores == serial.scores


def test_the_winner_does_not_depend_on_who_finishes_first():
    """Ties are broken by name, so a slow engine that would have tied does not
    lose for being slow. Collected in engine order rather than completion
    order, which is what makes the outcome a function of the portfolio."""
    problem = a_problem()

    def slow(_: Problem) -> Solution:
        time.sleep(0.2)
        return pyvrp_solve(problem)

    def quick(_: Problem) -> Solution:
        return pyvrp_solve(problem)

    first = run_portfolio(problem, [engine("a", slow), engine("b", quick)],
                          workers=2)
    second = run_portfolio(problem, [engine("a", quick), engine("b", slow)],
                           workers=2)

    assert first.winner == second.winner == "a", (
        "the winner changed with which engine was slower, so completion order "
        "is deciding rather than the objective")


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------

def test_an_engine_that_fails_does_not_take_the_concurrent_run_down():
    problem = a_problem()

    def explode(_: Problem) -> Solution:
        raise RuntimeError("engine went bang")

    outcome = run_portfolio(
        problem, [engine("bang", explode), *real_engines(2)], workers=3)

    assert "bang" in outcome.rejected
    assert outcome.winner is not None, "one failure lost the whole run"


def test_the_instance_is_not_mutated_by_a_parallel_run():
    """NFR-05's isolation, at the only level a library can check: the engines
    share the problem and none of them may change it."""
    problem = a_problem()
    before = problem.to_dict()

    run_portfolio(problem, real_engines(4), workers=4)

    assert problem.to_dict() == before


def test_a_zero_or_negative_worker_count_is_refused():
    """Nought workers is not "no parallelism", it is a pool that runs nothing,
    and a silently-clamped nought reads as a working run that solved no
    engines."""
    for workers in (0, -1):
        with pytest.raises(ValueError, match="workers"):
            run_portfolio(a_problem(), real_engines(1), workers=workers)


def test_the_report_is_ordered_by_the_portfolio_not_by_who_finished():
    """The winner is order-independent whatever happens: it is a `min` over a
    name-keyed dict with a name tiebreak. The *report* is not.

    Two runs that agree about every score and disagree about the order they are
    listed in serialise to different bytes, which is the same reproducibility
    problem `T-89`'s digest was built to avoid. Collecting in engine order is
    what makes the report a function of the portfolio.
    """
    problem = a_problem()

    def after(delay: float):
        def body(_: Problem) -> Solution:
            time.sleep(delay)
            return pyvrp_solve(problem)
        return body

    # Declared slowest-first, so completion order is the reverse of engine
    # order and the two are impossible to confuse.
    engines = [engine("a", after(0.30)), engine("b", after(0.15)),
               engine("c", after(0.01))]
    outcome = run_portfolio(problem, engines, workers=3)

    assert list(outcome.scores) == ["a", "b", "c"], (
        f"the report is ordered {list(outcome.scores)}, which is completion "
        "order; two runs of the same portfolio would serialise differently")
