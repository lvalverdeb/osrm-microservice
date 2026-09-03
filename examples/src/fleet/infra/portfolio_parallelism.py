"""Running the portfolio wide, and measuring whether it helped.

Demonstrates bounded intra-run parallelism landed for E-86/T-86 (NFR-05, §7.7):

    vrp.portfolio.run_portfolio(..., workers=N)

§7.7: "Intra-run parallelism: portfolio members on separate cores...
Reproducible mode (CON-4) forces single-threaded, iteration-limited execution."

Four things, in order:

1. **Proof that anything is concurrent at all.** A `workers` argument can be
   accepted and ignored, and every "same answer in parallel" test would still
   pass. Two engines meeting at a barrier cannot both arrive unless they are
   genuinely in flight together.

2. **The bound.** §7.7 says *bounded*, and an unbounded pool over a large
   portfolio is a fork bomb with a scoring function attached.

3. **The answer does not move.** Same winner, same scores, and the report
   listed in portfolio order rather than completion order -- so two runs
   serialise identically.

4. **The speed-up, measured rather than assumed** -- including the half of the
   portfolio that does not get one. Threads give separate cores only to engines
   that release the GIL.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/infra/portfolio_parallelism.py
"""

from __future__ import annotations

import math
import statistics
import sys
import threading
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
from vrp.portfolio import Portfolio, run_portfolio
from vrp.solve.pyvrp_adapter import solve as pyvrp_solve


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


def members(count: int, body) -> list[Portfolio]:
    return [Portfolio(name=f"e{index}", solve=body) for index in range(count)]


def proof_of_concurrency() -> None:
    heading("1.", "Proof that two engines are in flight at once")
    problem = a_real_round(stops=20, vans=3)

    def meet_at(barrier: threading.Barrier):
        def body(_: Problem) -> Solution:
            barrier.wait()
            return pyvrp_solve(problem)
        return body

    for workers, expected in ((2, "both arrive"), (1, "the first waits alone")):
        barrier = threading.Barrier(2, timeout=1.0)
        outcome = run_portfolio(problem, members(2, meet_at(barrier)),
                                workers=workers)
        got = "both arrived" if outcome.winner else "timed out"
        print(f"\n      workers={workers}: {expected:22s} -> {got}")
    print("\n   A pool of one is single-threaded, which is what CON-4's")
    print("   reproducible mode requires; the barrier is how you tell.")


def the_bound() -> None:
    heading("2.", "How many run at once")
    problem = a_real_round(stops=20, vans=3)
    live = peak = 0
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

    print(f"\n      {'bound':>6s} {'engines':>8s} {'peak in flight':>15s}")
    for workers in (1, 2, 4):
        live = peak = 0
        run_portfolio(problem, members(6, watched), workers=workers)
        print(f"      {workers:6d} {6:8d} {peak:15d}")


def the_answer_does_not_move() -> None:
    heading("3.", "The same portfolio, three widths")
    problem = a_real_round(stops=20, vans=3)

    def after(delay: float):
        def body(_: Problem) -> Solution:
            time.sleep(delay)
            return pyvrp_solve(problem)
        return body

    print(f"\n      {'workers':>8s} {'winner':>8s} {'report order':>26s}")
    for workers in (1, 2, 4):
        engines = [Portfolio(name="a", solve=after(0.20)),
                   Portfolio(name="b", solve=after(0.10)),
                   Portfolio(name="c", solve=after(0.01))]
        outcome = run_portfolio(problem, engines, workers=workers)
        print(f"      {workers:8d} {outcome.winner:>8s} "
              f"{list(outcome.scores)!s:>26s}")
    print("\n   'a' is declared first and finishes last, and the report still")
    print("   lists it first: two runs of one portfolio serialise the same.")


def the_speed_up() -> None:
    heading("4.", "What the parallelism is actually worth")
    # Sized so the example stays runnable: the pure-Python member is
    # deliberately slow, and it is timed three times at two widths.
    big = a_real_round(stops=60, vans=6)
    small = a_real_round(stops=12, vans=3)
    index = {loc.id: loc.matrix_index for loc in small.locations}
    stops = [index[(order.delivery or order.pickup).location_id]
             for order in small.orders]

    def python_bound(_: Problem) -> Solution:
        lns_search(small.matrix, [stops], iterations=4000, seed=1)
        return pyvrp_solve(small)

    def timed(problem, body, workers: int) -> float:
        runs = []
        for _ in range(3):
            started = time.perf_counter()
            run_portfolio(problem, members(4, body), workers=workers)
            runs.append(time.perf_counter() - started)
        return statistics.median(runs)

    print(f"\n      {'engine':34s} {'1 worker':>10s} {'4 workers':>10s} "
          f"{'speed-up':>9s}")
    for label, problem, body in (
            ("pyvrp (C++, releases the GIL)", big, pyvrp_solve),
            ("the repo's own LNS (pure Python)", small, python_bound)):
        serial = timed(problem, body, 1)
        wide = timed(problem, body, 4)
        print(f"      {label:34s} {serial:9.3f}s {wide:9.3f}s "
              f"{serial / wide:8.2f}x")

    print("\n   §7.7 asks for members on separate cores. With threads, only an")
    print("   engine that releases the GIL gets one -- so half this portfolio")
    print("   scales and half of it takes turns at one interpreter. Reporting")
    print("   the first number alone would have somebody size a box on a")
    print("   speed-up that only some engines can have. Processes are T-91.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-05 and §7.7. Default is one worker: CON-4's reproducible mode.")
    proof_of_concurrency()
    the_bound()
    the_answer_does_not_move()
    the_speed_up()
    print(f"\n{'=' * 72}")
    print("A `workers` argument that is accepted and ignored passes every test")
    print("about answers. The barrier is the one it cannot pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
