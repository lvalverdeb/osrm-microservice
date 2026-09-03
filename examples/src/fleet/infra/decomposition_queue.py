"""Solving the clusters at once, and finding out when that is worth doing.

Demonstrates the work queue landed for E-92/T-92 (NFR-05, §7.7):

    vrp.decompose.concatenate(..., workers=N)
    vrp.decompose.solve_decomposed(..., workers=N)

§7.7 names two kinds of intra-run parallelism: "portfolio members on separate
cores; decomposition sub-problems in a work queue". `T-86` delivered the first.
This is the second, and on a large instance it is the bigger of the two —
there are far more clusters than portfolio members.

Four things, in order:

1. **The plan does not move.** Same routes, same objective, at any width.

2. **Proof that anything is concurrent.** A `workers` argument can be accepted
   and ignored, and every "same plan" check would still pass.

3. **What makes the merge safe.** Not the collection order: every cluster owns
   its own vehicles, and that is the property the queue actually rests on.

4. **When it is worth it, measured.** On the repository's own decomposition
   fixture the queue is *slower*. It pays on instances big enough for the
   sub-solves to dominate the pool, which is what §7.7 is about — measured
   here on real Costa Rica deliveries, because how much work a cluster is
   depends on how the stops actually lie.

Runs offline. No gateway required: the matrix is planar over real
coordinates, which is what §7.6 asks for at this size anyway.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/infra/decomposition_queue.py
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

from vrp import decompose
from vrp.bench import fixtures
from vrp.decompose import concatenate, partition
from vrp.matrix import PlanarMatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
)

HOUR = 3600


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def a_real_round(stops: int, vehicles: int,
                 path: Path = None) -> Problem:
    """A round of real deliveries, nearest one depot first.

    Real coordinates rather than a scatter, because how much work a cluster is
    -- which is the whole question in section 4 -- depends on how the stops
    actually lie. Deliveries cluster along roads and around towns; a uniform
    scatter gives every cluster the same shape and the same cost, and the
    measurement would be about the generator.

    The matrix is planar over those coordinates rather than road-derived: §7.6
    wants a lazy matrix at this size, and this keeps the example offline.
    """
    corpus = dataset.load(path or dataset.DEFAULT_PATH)
    deliveries, depot = corpus.nearest(stops)
    day = TimeWindow(start=0, end=14 * HOUR)

    # Degrees to kilometres about the depot: longitude shortens with latitude.
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
            Order(id=f"O{i}", kind="JOB", quantities={"units": d["units"]},
                  delivery=StopSpec(location_id=d["product_id"],
                                    time_windows=(day,),
                                    service_fixed=d["service_minutes"] * 60))
            for i, d in enumerate(deliveries)),
        vehicles=tuple(
            Vehicle(id=f"V{n}", capacities={"units": heaviest * 30}, shift=day,
                    start_location_id="D", end_location_id="D")
            for n in range(1, vehicles + 1)),
        matrix=PlanarMatrix(version="real-v1", coordinates=tuple(coords)))


def the_plan_does_not_move() -> None:
    heading("1.", "The same clusters, three widths")
    problem = fixtures.uc074_at_the_decomposition_threshold()
    clusters = partition(problem, target_size=6)
    print(f"\n   {len(problem.orders)} orders in {len(clusters)} clusters\n")
    print(f"      {'workers':>8s} {'routes':>7s} {'objective':>12s}")
    for workers in (1, 2, 4):
        solution = concatenate(problem, clusters, seed=3, workers=workers)
        print(f"      {workers:8d} {len(solution.routes):7d} "
              f"{solution.objective_breakdown['total']:12,d}")


def proof_of_concurrency() -> None:
    heading("2.", "Proof that two clusters are solved at once")
    problem = fixtures.uc074_at_the_decomposition_threshold()
    clusters = partition(problem, target_size=6)[:2]
    real = decompose._solve_cluster

    for workers in (2, 1):
        barrier = threading.Barrier(2, timeout=1.0)

        def meeting(*args, _barrier=barrier, **kwargs):
            try:
                _barrier.wait()
            except threading.BrokenBarrierError:
                return {}
            return real(*args, **kwargs)

        decompose._solve_cluster = meeting
        try:
            concatenate(problem, clusters, seed=1, workers=workers)
            met = not barrier.broken
        finally:
            decompose._solve_cluster = real
        print(f"\n      workers={workers}: both clusters met at the barrier -> {met}")
    print("\n   A pool of one cannot pass it, which is what CON-4's")
    print("   reproducible mode requires of the default.")


def what_makes_the_merge_safe() -> None:
    heading("3.", "Why completion order cannot decide the plan")
    problem = fixtures.uc074_at_the_decomposition_threshold()
    clusters = partition(problem, target_size=6)
    print(f"\n      {'cluster':>8s} {'orders':>7s}  vehicles")
    for cluster in clusters:
        print(f"      {cluster.index:8d} {len(cluster.order_ids):7d}  "
              f"{', '.join(cluster.vehicle_ids)}")
    owned = [set(cluster.vehicle_ids) for cluster in clusters]
    disjoint = all(not a & b for i, a in enumerate(owned) for b in owned[i + 1:])
    print(f"\n   every cluster owns its own vehicles: {disjoint}")
    print("\n   The results are merged with `update`, so if two clusters named")
    print("   the same vehicle the last writer would win and the plan would")
    print("   depend on which finished first. They do not, and a test fails if")
    print("   that ever changes.")


def when_it_is_worth_it() -> None:
    heading("4.", "What the queue actually buys, measured")

    def timed(problem, clusters, workers: int) -> float:
        runs = []
        for _ in range(3):
            started = time.perf_counter()
            concatenate(problem, clusters, seed=3, workers=workers)
            runs.append(time.perf_counter() - started)
        return statistics.median(runs)

    small = fixtures.uc074_at_the_decomposition_threshold()
    cases = [("uc074 (the repo's own fixture)", small, partition(small, target_size=6))]
    for orders, vehicles, target in ((300, 20, 40), (600, 30, 60)):
        problem = a_real_round(orders, vehicles)
        cases.append((f"{orders} real deliveries", problem,
                      partition(problem, target_size=target)))

    print(f"\n      {'instance':32s} {'clusters':>8s} {'1w':>8s} {'4w':>8s} "
          f"{'speed-up':>9s}")
    for label, problem, clusters in cases:
        serial, wide = timed(problem, clusters, 1), timed(problem, clusters, 4)
        print(f"      {label:32s} {len(clusters):8d} {serial:7.3f}s "
              f"{wide:7.3f}s {serial / wide:8.2f}x")

    print("\n   The fixture the definition of done named is too small: its")
    print("   sub-solves take about two milliseconds each, so the pool costs")
    print("   more than it saves and the queue is slower than the loop. That")
    print("   is why the default is one worker, and why 'measure it' is not")
    print("   the same instruction as 'switch it on'.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-05 and §7.7. Default is one worker: CON-4's reproducible mode.")
    the_plan_does_not_move()
    proof_of_concurrency()
    what_makes_the_merge_safe()
    when_it_is_worth_it()
    print(f"\n{'=' * 72}")
    print("Parallelism that is slower on the instance you have is not a")
    print("speed-up. The number is the deliverable, not the thread pool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
