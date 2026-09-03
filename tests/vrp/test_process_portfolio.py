"""Process-based parallelism for engines that hold the GIL — NFR-05, §7.7, T-91.

`T-86` gave the portfolio a bounded thread pool and measured what it was worth:
3.00x for PyVRP, which is C++ and releases the GIL, and **1.00x** for the
repository's own pure-Python LNS, which does not. §7.7 asks for "portfolio
members on separate cores", and half the portfolio was not getting one.

Measured before this was built, on four members of a pure-Python engine:

    serial                    2.664s   1.00x
    threads, 4 workers        2.624s   1.02x
    processes, 4 workers      0.788s   3.38x

**Two constraints come with processes and neither is optional.** An engine must
be importable by a worker, so a lambda or a closure cannot be sent — refused by
name here rather than surfacing as `BrokenProcessPool` three frames into the
standard library. And the calling program needs the `if __name__ == "__main__"`
guard, because a spawned worker re-imports it; a library cannot fix that, only
say so.
"""

from __future__ import annotations

import pytest

from vrp.bench import fixtures
from vrp.model import Problem, Solution
from vrp.portfolio import Portfolio, UnsendableEngine, run_portfolio
from vrp.solve.pyvrp_adapter import solve as pyvrp_solve


def a_problem() -> Problem:
    return fixtures.uc075_delivery_station_sequencing()


# Module-level so a spawned worker can import them. A closure could not be
# sent, which is the constraint these tests exist to pin.
def solve_it(problem: Problem) -> Solution:
    return pyvrp_solve(problem)


def stamp_its_pid(problem: Problem) -> Solution:
    """Solve, and record which interpreter did it."""
    import dataclasses
    import os

    solution = pyvrp_solve(problem)
    return dataclasses.replace(solution, solver={"pid": os.getpid()})


def decline_it(problem: Problem) -> Solution:
    raise NotImplementedError("this engine declines every instance")


def members(count: int) -> list[Portfolio]:
    return [Portfolio(name=f"e{index}", solve=solve_it) for index in range(count)]


# --------------------------------------------------------------------------
# The answer is the same wherever it is computed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("executor", ["thread", "process"])
def test_the_winner_and_the_report_match_the_serial_run(executor):
    problem = a_problem()
    serial = run_portfolio(problem, members(3), workers=1)
    wide = run_portfolio(problem, members(3), workers=3, executor=executor)

    assert wide.winner == serial.winner
    assert wide.scores == serial.scores
    assert list(wide.scores) == list(serial.scores), (
        "the report is in a different order, so two runs serialise differently")


def test_a_declining_engine_in_a_worker_process_is_rejected_not_fatal():
    """§7.3 runs several engines precisely so one can fail. An exception
    crossing a process boundary must arrive as a rejection."""
    problem = a_problem()
    portfolio = [Portfolio(name="declines", solve=decline_it), *members(2)]

    outcome = run_portfolio(problem, portfolio, workers=3, executor="process")

    assert "declines" in outcome.rejected
    assert "NotImplementedError" in outcome.rejected["declines"]
    assert outcome.winner is not None, "one failure lost the whole run"


def test_the_work_really_happens_in_another_interpreter():
    """The direct proof, and the one a silent fallback to threads fails.

    Threads share a process; processes do not. An engine that stamps its own
    pid into the solution says which it was, and no amount of "the answer is
    the same" can substitute for it.
    """
    import os

    problem = a_problem()
    portfolio = [Portfolio(name=f"p{i}", solve=stamp_its_pid) for i in range(3)]

    threaded = run_portfolio(problem, portfolio, workers=3, executor="thread")
    processed = run_portfolio(problem, portfolio, workers=3,
                              executor="process")

    assert threaded.best.solver["pid"] == os.getpid(), (
        "a thread ran in another process, which is not what threads are")
    assert processed.best.solver["pid"] != os.getpid(), (
        "the process executor ran the engine in this interpreter, so it is a "
        "thread pool wearing another name")


# --------------------------------------------------------------------------
# What processes cannot carry
# --------------------------------------------------------------------------

def test_an_engine_a_worker_could_not_import_is_refused_by_name():
    """The failure a caller would otherwise meet as `BrokenProcessPool`.

    Refused before the pool starts, naming the engine, because the standard
    library's version of this error says nothing about which member of the
    portfolio was at fault.
    """
    problem = a_problem()
    portfolio = [Portfolio(name="a-closure", solve=lambda p: pyvrp_solve(p))]

    with pytest.raises(UnsendableEngine, match="a-closure"):
        run_portfolio(problem, portfolio, workers=2, executor="process")


def test_the_same_closure_is_fine_in_a_thread():
    """The constraint belongs to processes, not to parallelism."""
    problem = a_problem()
    portfolio = [Portfolio(name="a-closure", solve=lambda p: pyvrp_solve(p))]

    outcome = run_portfolio(problem, portfolio, workers=2, executor="thread")
    assert outcome.winner == "a-closure"


def test_one_worker_runs_inline_whatever_the_executor_says():
    """CON-4's reproducible mode is single-threaded and single-process: an
    unsendable engine must not be refused when nothing is being sent."""
    problem = a_problem()
    portfolio = [Portfolio(name="a-closure", solve=lambda p: pyvrp_solve(p))]

    outcome = run_portfolio(problem, portfolio, workers=1, executor="process")
    assert outcome.winner == "a-closure"


def test_an_unknown_executor_is_refused():
    with pytest.raises(ValueError, match="executor"):
        run_portfolio(a_problem(), members(1), workers=2, executor="magic")
