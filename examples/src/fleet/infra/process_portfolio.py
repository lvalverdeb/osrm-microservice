"""Giving a pure-Python engine a core of its own.

Demonstrates process-based parallelism landed for E-91/T-91 (NFR-05, §7.7):

    vrp.portfolio.run_portfolio(..., workers=N, executor="process")

`T-86` gave the portfolio a bounded thread pool and measured what it bought:
3.00x for PyVRP, which is C++ and releases the GIL, and **1.00x** for the
repository's own LNS, which does not. §7.7 asks for "portfolio members on
separate cores" and half the portfolio was not getting one.

Four things, in order:

1. **What a thread pool cannot fix.** The same pure-Python work, serial and
   across four threads.

2. **What a process pool does.** The same work again, on four interpreters.

3. **Proof it is really another interpreter** — an engine that stamps its own
   process id, because "the answer is the same" is exactly what a silent
   fallback to threads would also give.

4. **What it costs you as a caller.** An engine a worker cannot import is
   refused by name, and your program needs the `__main__` guard.

Runs offline. Must be run as a script rather than imported: a spawned worker
re-imports the main module, which is the constraint section 4 is about.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/infra/process_portfolio.py
"""

from __future__ import annotations

import dataclasses
import math
import os
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.lns import lns_search
from vrp.matrix import PlanarMatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    Solution,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.portfolio import Portfolio, UnsendableEngine, run_portfolio
from vrp.solve.pyvrp_adapter import solve as pyvrp_solve

MEMBERS = 4
ITERATIONS = 6000


def python_bound(problem: Problem) -> Solution:
    """A member that does its thinking in Python, and holds the GIL doing it."""
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    stops = [index[(order.delivery or order.pickup).location_id]
             for order in problem.orders]
    lns_search(problem.matrix, [stops], iterations=ITERATIONS, seed=1)
    return pyvrp_solve(problem)


def stamp_its_pid(problem: Problem) -> Solution:
    return dataclasses.replace(pyvrp_solve(problem), solver={"pid": os.getpid()})



def a_real_round(stops: int, vans: int) -> Problem:
    """A day of real deliveries around one depot, priced planar.

    Real coordinates rather than a fixture: the timings below are about how
    much work a member is, and real stops cluster along roads and around towns
    in a way a uniform scatter does not.
    """
    corpus = dataset.load(dataset.DEFAULT_PATH)
    deliveries, depot = corpus.nearest(stops)
    day = TimeWindow(start=0, end=14 * 3600)

    lat_km = 110.57
    lon_km = 111.32 * math.cos(math.radians(depot["latitude"]))
    coords = [(0.0, 0.0)] + [
        ((d["longitude"] - depot["longitude"]) * lon_km,
         (d["latitude"] - depot["latitude"]) * lat_km) for d in deliveries]

    heaviest = max((d["units"] for d in deliveries), default=1)
    return Problem(
        id=f"real-{stops}",
        locations=(Location(id="D", lat=depot["latitude"],
                            lon=depot["longitude"], matrix_index=0),) + tuple(
            Location(id=d["product_id"], lat=d["latitude"], lon=d["longitude"],
                     matrix_index=i + 1)
            for i, d in enumerate(deliveries)),
        orders=tuple(
            Order(id=f"O{i + 1}", kind="JOB", quantities={"units": d["units"]},
                  delivery=StopSpec(location_id=d["product_id"],
                                    time_windows=(day,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=tuple(
            Vehicle(id=f"V{n}", capacities={"units": heaviest * 30}, shift=day,
                    start_location_id="D", end_location_id="D")
            for n in range(1, vans + 1)),
        matrix=PlanarMatrix(version="real-v1", coordinates=tuple(coords)))


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def timed(problem: Problem, workers: int, executor: str) -> float:
    portfolio = [Portfolio(name=f"e{i}", solve=python_bound)
                 for i in range(MEMBERS)]
    runs = []
    for _ in range(3):
        started = time.perf_counter()
        run_portfolio(problem, portfolio, workers=workers, executor=executor)
        runs.append(time.perf_counter() - started)
    return statistics.median(runs)


def the_measurement(problem: Problem) -> None:
    heading("1.", "What a thread pool cannot fix")
    serial = timed(problem, 1, "thread")
    threaded = timed(problem, MEMBERS, "thread")
    print(f"\n      {'run':28s} {'time':>9s} {'speed-up':>9s}")
    print(f"      {'serial (1 worker)':28s} {serial:8.3f}s {1.0:8.2f}x")
    print(f"      {f'{MEMBERS} threads':28s} {threaded:8.3f}s "
          f"{serial / threaded:8.2f}x")
    print("\n   Four threads, one interpreter, four members taking turns.")

    heading("2.", "What a process pool does with the same work")
    processed = timed(problem, MEMBERS, "process")
    print(f"\n      {f'{MEMBERS} processes':28s} {processed:8.3f}s "
          f"{serial / processed:8.2f}x")
    print("\n   Same engine, same seeds, same answer. The difference is that")
    print("   each member now has an interpreter to itself.")


def proof_of_separate_interpreters(problem: Problem) -> None:
    heading("3.", "Proof that it is really another interpreter")
    portfolio = [Portfolio(name=f"p{i}", solve=stamp_its_pid) for i in range(3)]
    print(f"\n   this process: {os.getpid()}\n")
    print(f"      {'executor':10s} {'engine ran in':>14s}")
    for executor in ("thread", "process"):
        outcome = run_portfolio(problem, portfolio, workers=3,
                                executor=executor)
        print(f"      {executor:10s} {outcome.best.solver['pid']:14d}")
    print("\n   'The answer is the same' is what a silent fallback to threads")
    print("   would also give you. The pid is what tells them apart.")


def what_it_costs_the_caller(problem: Problem) -> None:
    heading("4.", "The two things processes ask of you")
    try:
        run_portfolio(problem,
                      [Portfolio(name="a-closure", solve=lambda p: pyvrp_solve(p))],
                      workers=2, executor="process")
    except UnsendableEngine as refusal:
        import textwrap
        print()
        for line in textwrap.wrap(str(refusal), width=64):
            print(f"      {line}")
    print("\n   And the other: a spawned worker re-imports your main module,")
    print("   so a program that starts a solve at import time starts one in")
    print("   every worker. That needs `if __name__ == \"__main__\"`, which no")
    print("   library can add on your behalf — this file has it below.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-05 and §7.7. Threads for C++ engines; processes for the rest.")
    problem = a_real_round(stops=12, vans=3)
    the_measurement(problem)
    proof_of_separate_interpreters(problem)
    what_it_costs_the_caller(problem)
    print(f"\n{'=' * 72}")
    print("A thread pool round a pure-Python engine is four members queueing")
    print("politely for one core. The measurement is how you find that out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
