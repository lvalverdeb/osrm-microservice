"""What a run has to be able to tell you afterwards.

Demonstrates the run record landed for E-87/T-87 (NFR-06, CON-4):

    vrp.observe   the seven things NFR-06 asks every run to emit

`NFR-06`: "Every run emits: objective trajectory over time, incumbent
timestamps, constraint-violation counts, matrix cache hit rate, seed, solver
version, deterministic iteration count."

The solver record carried three of them. The other four existed in pieces that
nothing collected: the pair cache counts its own hits, the independent verifier
returns violations, and the trajectory was not recorded anywhere at all.

Four things, in order:

1. **A real run, watched.** The trajectory comes from `lns_search` reporting
   each new incumbent, not from anything assembling a plausible-looking list.

2. **Two clocks, one of which replays.** NFR-06 wants timestamps; CON-4 wants
   reproducibility. Wall-clock nanoseconds give the first and destroy the
   second, so the record keeps both and names which half is a function of the
   seed.

3. **The fields that are easy to fake.** The hit rate and the violation count
   are shown moving — a record whose fields never move is a record of nothing,
   and that is the failure mode this requirement invites.

4. **Watching costs nothing.** The same seed with and without a recorder
   returns the same plan, because observability that changes the answer is
   worse than none.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/infra/run_record.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

from vrp.bench import fixtures
from vrp.lns import lns_search, plan_cost
from vrp.matrix import PairCache, PlanarMatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.observe import NFR_06_FIELDS, Recorder
from vrp.verify import verify

ITERATIONS = 400
SEED = 4


def a_round():
    """A real day's deliveries, as one long route for the search to improve."""
    problem = a_real_round(stops=40, vans=4)
    index = {location.id: location.matrix_index for location in problem.locations}
    stops = [index[(order.delivery or order.pickup).location_id]
             for order in problem.orders]
    return problem, [stops]



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


def a_watched_run():
    heading("1.", "A run, and what it was able to say about itself")
    problem, plan = a_round()
    cache = PairCache()
    recorder = Recorder(solver="lns:sisr", seed=SEED)
    best = lns_search(problem.matrix, plan, iterations=ITERATIONS, seed=SEED,
                      recorder=recorder)
    record = recorder.finish(iterations=ITERATIONS, cache=cache)

    print(f"\n   {plan_cost(problem.matrix, plan):,} -> "
          f"{plan_cost(problem.matrix, best):,} over {ITERATIONS} iterations\n")
    print(f"      {'iteration':>10s} {'at':>10s} {'objective':>12s}")
    for point in record.trajectory:
        print(f"      {point.iteration:10d} {point.elapsed_ns / 1e6:8.2f}ms "
              f"{point.objective:12,d}")
    print(f"\n   NFR-06 names seven things. The record has: "
          f"{', '.join(NFR_06_FIELDS[:4])},")
    print(f"   {', '.join(NFR_06_FIELDS[4:])}.")
    return record


def two_clocks() -> None:
    heading("2.", "Which half of the record replays")
    problem, plan = a_round()

    def run():
        recorder = Recorder(solver="lns:sisr", seed=SEED)
        lns_search(problem.matrix, plan, iterations=ITERATIONS, seed=SEED,
                   recorder=recorder)
        return recorder.finish(iterations=ITERATIONS, cache=PairCache())

    first, second = run(), run()
    print("\n   same seed, two runs:\n")
    print(f"      whole record identical:      {first == second}")
    print(f"      replayable half identical:   "
          f"{first.replayable() == second.replayable()}")
    print(f"\n      run 1 first incumbent at {first.trajectory[0].elapsed_ns:,} ns")
    print(f"      run 2 first incumbent at {second.trajectory[0].elapsed_ns:,} ns")
    print("\n   Comparing whole records would report every pair of runs as")
    print("   different, for a reason that says nothing about the plan.")


def the_fields_that_are_easy_to_fake() -> None:
    heading("3.", "The hit rate and the violation count, moving")
    cold, warm = PairCache(), PairCache()
    pair = ((9.9, -84.0), (9.91, -84.01), "driving")
    warm.put(*pair[:2], pair[2], 120, 1_000)
    for cache in (cold, warm):
        cache.get(*pair[:2], pair[2])

    print(f"\n      {'cache':10s} {'lookups':>8s} {'hits':>6s} {'rate':>8s}")
    for label, cache in (("cold", cold), ("warm", warm)):
        record = Recorder(solver="lns", seed=0).finish(1, {}, cache)
        print(f"      {label:10s} {record.cache_lookups:8d} "
              f"{record.cache_hits:6d} {record.cache_hit_rate_ppt / 10:6.1f}%")

    problem = fixtures.uc070_single_order_single_vehicle()
    vehicle = problem.vehicles[0]
    order = problem.orders[0]
    site = (order.delivery or order.pickup).location_id
    very_late = 10 ** 7
    late = Solution(
        problem_id=problem.id, status="FEASIBLE", unassigned=(),
        routes=(Route(vehicle_id=vehicle.id, steps=(
            Step(type="START", location_id=vehicle.start_location_id,
                 arrival=0, start_service=0, departure=0),
            Step(type="DELIVERY", location_id=site, order_id=order.id,
                 arrival=very_late, start_service=very_late,
                 departure=very_late),
            Step(type="END", location_id=vehicle.start_location_id,
                 arrival=very_late, start_service=very_late,
                 departure=very_late))),))
    counted = Recorder.violations_of(verify(problem, late))
    print(f"\n      a plan that arrives a hundred days late: {counted}")
    print("      (counted by the independent verifier, not by whatever")
    print("      produced the plan — which is what CON-1 keeps apart)")


def watching_costs_nothing() -> None:
    heading("4.", "The recorder does not change the answer")
    problem, plan = a_round()
    watched = lns_search(problem.matrix, plan, iterations=ITERATIONS, seed=SEED,
                         recorder=Recorder(solver="lns", seed=SEED))
    plain = lns_search(problem.matrix, plan, iterations=ITERATIONS, seed=SEED)
    print(f"\n   same plan with and without a recorder: {watched == plain}")
    print("\n   The search reports what it found and knows nothing about what")
    print("   is done with it, so observability cannot bend the result it is")
    print("   supposed to be describing.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-06 and CON-4. Three of the seven existed; four did not.")
    a_watched_run()
    two_clocks()
    the_fields_that_are_easy_to_fake()
    watching_costs_nothing()
    print(f"\n{'=' * 72}")
    print("Every one of these is easy to emit and hard to make true.")
    print("A field that never moves is a field nobody can use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
