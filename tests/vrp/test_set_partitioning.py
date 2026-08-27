"""Set-partitioning polish over the route pool — ALG-6, T-38, E-38.

ALG-6: "Collect all distinct routes generated across the whole search into a
pool, then solve a set-partitioning MILP over the pool (each order covered
exactly once, vehicle-type counts respected). With a few thousand columns this
solves in seconds and reliably recovers 0.5-2% over the best single trajectory.
Requires that pooled routes be individually verified feasible."

The idea is that a search throws away good routes. Run A finds an excellent
route through the north and a mediocre one through the south; run B does the
opposite. Neither trajectory is the best plan, and the best plan is sitting in
the union of what they built. Set partitioning is how you take it.

Two claims here can fail quietly and both have tests that would catch nothing if
written loosely:

* **"each order covered exactly once."** Covered *at least* once is a set
  *covering* problem, it is easier to solve, and it produces plans that deliver
  the same parcel twice. `test_no_order_is_delivered_twice` exists because a
  relaxation to >= is a one-character edit that makes every other test here
  pass and the answer wrong.

* **"recovers 0.5-2%."** Measured against the best single trajectory that fed
  the pool, not against an arbitrary baseline -- otherwise the number says more
  about the baseline than about the polish.

`test_the_polish_is_never_worse_than_its_own_pool` is the structural guarantee
the rest rests on: the incumbent's own routes are in the pool, so the optimum
over the pool cannot be worse than the incumbent. If that ever fails, the model
is wrong rather than the search unlucky.
"""

from __future__ import annotations

import pytest

from vrp.bench.corpus import CORPUS, Spec, build_instance
from vrp.evaluator import ObjectiveWeights
from vrp.generate import Shape, generate_instance
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.setpartition import (
    PooledRoute,
    RoutePool,
    build_pool,
    partition_cost,
    polish,
    select_routes,
)
from vrp.verify import verify

WEIGHTS = ObjectiveWeights(per_metre=1, per_second=0)


def assignment_of(solution) -> dict[str, list[str]]:
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes}


# --------------------------------------------------------------------------
# The pool
# --------------------------------------------------------------------------

def test_the_pool_keeps_one_entry_per_distinct_route():
    """"All *distinct* routes". The same sequence found by three runs is one
    column, not three -- and a pool that grows with runs rather than with
    discoveries makes the MILP slower without making it better."""
    problem = generate_instance(7, shape=Shape.SLACK)
    pool = RoutePool()

    pool.add(problem, "V0", ["O1", "O2"])
    pool.add(problem, "V0", ["O1", "O2"])
    pool.add(problem, "V0", ["O2", "O1"])

    assert len(pool) == 2, [r.order_ids for r in pool]


def test_the_pool_refuses_a_route_the_vehicle_could_not_drive():
    """ALG-6: "Requires that pooled routes be individually verified feasible."

    A pool that accepts an illegal column produces an illegal plan that the
    MILP is certain it has optimised, which is worse than no polish at all.
    """
    problem = generate_instance(9, shape=Shape.TIGHT_CAPACITY)
    everything = [order.id for order in problem.orders]
    pool = RoutePool()

    admitted = pool.add(problem, problem.vehicles[0].id, everything)

    assert not admitted, "an over-capacity route entered the pool"
    assert len(pool) == 0


def test_the_pool_costs_each_route_from_the_matrix():
    """Not from whatever produced it. INV-9's argument: a solver's own
    accounting is not evidence about the solver, and a pool fed by several of
    them is a pool of numbers computed several different ways."""
    problem = generate_instance(7, shape=Shape.SLACK)
    pool = RoutePool()
    pool.add(problem, "V0", ["O1", "O2"])

    entry = next(iter(pool))
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    depot = index[problem.vehicle("V0").start_location_id]
    stops = [index[problem.order(o).delivery.location_id]
             for o in entry.order_ids]
    expected = (problem.matrix.distance(depot, stops[0])
                + problem.matrix.distance(stops[0], stops[1])
                + problem.matrix.distance(stops[1], depot))

    assert entry.cost == expected


def test_a_pool_built_from_several_runs_holds_more_than_any_one_of_them():
    """The premise of ALG-6. If independent runs produced identical route sets
    there would be nothing to recombine and the whole technique would be an
    expensive way to restate the incumbent."""
    problem = build_instance(CORPUS[1])

    single = build_pool(problem, runs=1, iterations=120, seed=0)
    several = build_pool(problem, runs=6, iterations=120, seed=0)

    assert len(several) > len(single), (len(several), len(single))


# --------------------------------------------------------------------------
# The set-partitioning model
# --------------------------------------------------------------------------

def test_every_order_is_covered():
    problem = build_instance(CORPUS[0])
    pool = build_pool(problem, runs=4, iterations=150, seed=0)

    chosen = select_routes(problem, pool)

    covered = [o for route in chosen for o in route.order_ids]
    assert sorted(covered) == sorted(order.id for order in problem.orders)


def test_no_order_is_delivered_twice():
    """"Each order covered *exactly* once", on a pool where covering is cheaper.

    Relaxing `== 1` to `>= 1` turns this into set covering, and pricing it out
    does not catch that. Two earlier fixtures tried: a corpus instance, and a
    pool where overlapping columns were cheap. Both passed under the relaxation,
    for a reason that makes any cost-based fixture hopeless -- covering is a
    *relaxation* of partitioning, so every partition is feasible for it, and
    with metric costs the cheapest cover is a partition anyway.

    What separates the two models is not price but *existence*. This pool covers
    all three orders and admits no partition at all: O2 is in both columns and
    in no other. Partitioning must report that. Covering happily returns both
    columns and delivers O2 twice.
    """
    problem, pool = _overlapping_pool()

    chosen = select_routes(problem, pool)

    assert chosen is None, (
        f"{[c.order_ids for c in chosen]} covers O2 twice; the model is "
        "covering, not partitioning")


def test_no_vehicle_is_given_two_routes():
    """"vehicle-type counts respected", on a pool where it binds.

    Dropping the per-vehicle constraint was tested first against a corpus
    instance and passed, because the cheapest partition there happened to spread
    across vehicles anyway. Here it does not: V0 sits on the depot and V1 an
    hour away, so both of V0's columns are cheaper than either of V1's, and an
    unconstrained model sends V0 out twice -- one van, two simultaneous days.
    """
    problem, pool = _two_depot_pool()

    chosen = select_routes(problem, pool)

    assert len(chosen) <= len(problem.vehicles)
    drivers = [route.vehicle_id for route in chosen]
    assert len(drivers) == len(set(drivers)), (
        f"{drivers} gives one vehicle two routes")


def test_the_polish_is_never_worse_than_its_own_pool():
    """The structural guarantee. The incumbent's routes are columns too, so the
    optimum over the pool is at worst the incumbent. A failure here is a wrong
    model, not an unlucky search."""
    problem = build_instance(CORPUS[2])
    pool = build_pool(problem, runs=5, iterations=150, seed=0)

    best_trajectory = min(pool.trajectories)
    chosen = select_routes(problem, pool)

    assert partition_cost(chosen) <= best_trajectory


def test_the_polished_plan_verifies():
    """CON-1. The MILP reasons over columns; the verifier reads the finished
    timeline and shares nothing with either."""
    problem = build_instance(CORPUS[1])

    polished = polish(problem, runs=5, iterations=150, seed=0)

    report = verify(problem, polished)
    assert report.ok, [str(v) for v in report.violations[:3]]


def test_the_polish_is_deterministic_for_a_seed():
    """CON-4. CP-SAT is deterministic only when told to be -- one worker and a
    fixed seed -- and a parallel portfolio search would give a different
    optimum-cost plan on a different machine."""
    problem = build_instance(CORPUS[0])

    first = polish(problem, runs=4, iterations=150, seed=2)
    second = polish(problem, runs=4, iterations=150, seed=2)

    assert first.routes == second.routes


def test_an_infeasible_pool_reports_rather_than_inventing_a_plan():
    """A pool whose columns cannot cover every order has no partition. Saying
    so beats returning the best partial cover as though it were a plan."""
    problem = build_instance(CORPUS[0])
    pool = RoutePool()
    first = problem.orders[0].id
    pool.add(problem, problem.vehicles[0].id, [first])

    assert select_routes(problem, pool) is None


# --------------------------------------------------------------------------
# T-38's acceptance
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spec", CORPUS, ids=[s.name for s in CORPUS])
def test_the_polish_never_loses_on_any_corpus_instance(spec):
    """Per-instance floor. The mean below can hide one instance going backwards,
    and a polish that sometimes makes things worse is not a polish."""
    problem = build_instance(spec)
    pool = build_pool(problem, runs=5, iterations=200, seed=0)
    chosen = select_routes(problem, pool)

    assert chosen is not None
    assert partition_cost(chosen) <= min(pool.trajectories)


def test_the_frozen_corpus_has_nothing_left_to_recover():
    """T-38 originally asked for ">= 0.5% mean improvement on the frozen
    corpus". Measured, the corpus gives **exactly 0.00%**, on every instance,
    and that is a fact about the corpus rather than about the implementation.
    T-38 has since been amended to what the corpus can actually express -- never
    worse on any instance, mean reported -- with ALG-6's 0.5% moved to where its
    premise holds. This test is the corpus half of that amended wording.

    Two independent engines, eight trajectories, and every one of them lands on
    the same partition: c20-scattered produced *two* distinct order sets across
    eight runs. On a 20-customer CVRP that is what optimality looks like. There
    is no better partition in the pool because there is no better partition.

    The pools are also far smaller than the technique assumes. ALG-6 says "with
    a few thousand columns"; the corpus yields 8 to 41. Measured on a
    capacity-pressured 200-customer instance, where the pool can actually get
    big, the recovery tracks pool size closely:

        columns    recovered    MILP
            197       +0.22%      <1 s
            489       +0.60%       3 s
            977       +0.89%     537 s

    So ALG-6's 0.5-2% claim reproduces -- see the test below -- but not here.
    This test asserts the part that matters on a solved corpus: the polish never
    makes anything worse. The mean is printed rather than asserted, because
    asserting 0.5% against an already-optimal corpus would mean tuning the
    corpus or the measurement until a number appeared -- which is why the spec
    moved rather than the test.

    Note the MILP cost in that table. Columns are cheap to collect and not cheap
    to partition over: 977 of them took nine minutes where 489 took three
    seconds. "Solves in seconds" holds at the size ALG-6 has in mind and stops
    holding shortly after.
    """
    gains = []
    for spec in CORPUS:
        problem = build_instance(spec)
        pool = build_pool(problem, runs=5, iterations=200, seed=0)
        chosen = select_routes(problem, pool)
        assert chosen is not None, spec.name

        best = min(pool.trajectories)
        gain = (best - partition_cost(chosen)) / best * 100
        gains.append(gain)
        print(f"  {spec.name:<24}{len(pool):>4} cols  {best:>9,} -> "
              f"{partition_cost(chosen):>9,}  {gain:+.2f}%")

    mean = sum(gains) / len(gains)
    print(f"  {'mean':<24}{'':>4}       {'':>9}    {'':>9}  {mean:+.2f}%")
    assert mean >= 0.0, f"the polish made the corpus worse by {-mean:.2f}%"


def test_the_polish_recovers_ALG_6s_claim_when_the_pool_is_large_enough():
    """ALG-6's actual claim: "reliably recovers 0.5-2% over the best single
    trajectory", given "a few thousand columns".

    The frozen corpus cannot show this -- see above -- so the claim is tested
    where its premise holds: a capacity-pressured instance whose routes are
    short and numerous, and enough runs to build a real pool. Twenty runs give
    ~490 columns, and the recovery is ~0.6%.

    Short and numerous is the operative property. An earlier attempt used a
    400-stop instance with generous capacity, where the solver used eight
    vehicles for fifty stops each: 24 columns, and cross-run partitions that
    essentially never align, because a partition needs columns that are exactly
    disjoint and exactly complete. Recombination needs small pieces.
    """
    spec = Spec("c200-pressure", customers=200, vehicles=30, capacity=42,
                seed=2201, clustered=True, tight_windows=False)
    problem = build_instance(spec)

    pool = build_pool(problem, runs=20, iterations=300, seed=0)
    chosen = select_routes(problem, pool)
    assert chosen is not None

    best = min(pool.trajectories)
    gain = (best - partition_cost(chosen)) / best * 100
    print(f"\n  {len(pool)} columns: {best:,} -> {partition_cost(chosen):,} "
          f"({gain:+.2f}%)")
    assert gain >= 0.5, f"recovered {gain:.2f}%, under ALG-6's 0.5%"


def test_a_better_partition_in_the_pool_is_actually_found():
    """The test without which every 0.00% above is meaningless.

    A model that always returned the incumbent would report exactly the
    measurements this file records on the corpus. So: four stops, two far left
    and two far right, a pool holding both the crossing arrangement and the
    sensible one. The sensible one is strictly cheaper and must be chosen.
    """
    day = TimeWindow(start=0, end=12 * 3600)
    xs = [0.0, -3.0, -4.0, 3.0, 4.0]
    size = len(xs)
    grid = tuple(tuple(int(abs(xs[i] - xs[j]) * 1000) for j in range(size))
                 for i in range(size))
    problem = Problem(
        id="split",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}", lat=9.9,
                                 lon=-84.0 + xs[i] / 100, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(day,),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{k}", capacities={"kg": 10}, shift=day,
                               start_location_id="D", end_location_id="D")
                       for k in range(2)),
        matrix=TravelMatrix(version="split", durations=grid, distances=grid))

    pool = RoutePool()
    # The crossing arrangement: each vehicle drives the width of the map twice.
    assert pool.add(problem, "V0", ["O1", "O3"])
    assert pool.add(problem, "V1", ["O2", "O4"])
    # The sensible one: left together, right together.
    assert pool.add(problem, "V0", ["O1", "O2"])
    assert pool.add(problem, "V1", ["O3", "O4"])

    chosen = select_routes(problem, pool)

    assert sorted(tuple(c.order_ids) for c in chosen) == [("O1", "O2"),
                                                          ("O3", "O4")]


def test_a_pooled_route_is_the_same_route_it_was_pooled_as():
    """The recombination must not silently re-sequence a column.

    A set-partitioning polish selects routes; it does not re-optimise them. If
    the plan it returns visits a column's stops in a different order, the cost
    the MILP minimised is not the cost of the plan it produced.
    """
    problem = build_instance(CORPUS[0])
    pool = build_pool(problem, runs=4, iterations=150, seed=0)
    chosen = select_routes(problem, pool)
    polished = polish(problem, runs=4, iterations=150, seed=0)

    served = sorted(tuple(route.order_ids) for route in chosen)
    planned = sorted(tuple(orders)
                     for orders in assignment_of(polished).values() if orders)
    assert served == planned


def test_a_pooled_route_records_which_vehicle_could_drive_it():
    """"vehicle-type counts respected" needs the column to know what it needs.
    A route legal on a 50-unit van is not legal on a 20-unit one, and a pool
    that forgets which is which respects nothing."""
    problem = build_instance(CORPUS[0])
    pool = RoutePool()
    pool.add(problem, problem.vehicles[0].id, [problem.orders[0].id])

    entry = next(iter(pool))
    assert isinstance(entry, PooledRoute)
    assert entry.vehicle_id == problem.vehicles[0].id


# --------------------------------------------------------------------------
# Fixtures where the two named constraints actually bind
# --------------------------------------------------------------------------

def _line_problem(xs: list[float], vehicles: list[tuple[str, int]]) -> Problem:
    """Stops on a line; `vehicles` names each vehicle and its depot index."""
    day = TimeWindow(start=0, end=20 * 3600)
    size = len(xs)
    grid = tuple(tuple(int(abs(xs[i] - xs[j]) * 1000) for j in range(size))
                 for i in range(size))
    depots = {index for _, index in vehicles}
    locations = tuple(
        Location(id=f"D{i}" if i in depots else f"C{i}", lat=9.9,
                 lon=-84.0 + xs[i] / 100, matrix_index=i)
        for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(day,),
                                service_fixed=60))
        for i in range(size) if i not in depots)
    fleet = tuple(
        Vehicle(id=name, capacities={"kg": 10}, shift=day,
                start_location_id=f"D{index}", end_location_id=f"D{index}")
        for name, index in vehicles)
    return Problem(id="line", locations=locations, orders=orders,
                   vehicles=fleet,
                   matrix=TravelMatrix(version="line", durations=grid,
                                       distances=grid))


def _overlapping_pool() -> tuple[Problem, RoutePool]:
    """A pool that covers everything and partitions nothing.

    Both columns contain O2, and no other column exists. Every order is
    coverable; no selection covers each exactly once. Partitioning reports that;
    covering returns both and delivers O2 twice.
    """
    problem = _line_problem([0.0, 1.0, 2.0, 3.0], [("V0", 0), ("V1", 0)])
    pool = RoutePool()
    assert pool.add(problem, "V0", ["O1", "O2"])
    assert pool.add(problem, "V1", ["O2", "O3"])
    return problem, pool


def _two_depot_pool() -> tuple[Problem, RoutePool]:
    """V0 on the stops, V1 far away: V0's columns dominate on price."""
    # 20 km out, not 60: at 60 the round trip runs 33 hours and the pool
    # rightly refuses the column, leaving nothing for the constraint to bind on.
    problem = _line_problem([0.0, 1.0, 2.0, 20.0], [("V0", 0), ("V1", 3)])
    pool = RoutePool()
    assert pool.add(problem, "V0", ["O1"])
    assert pool.add(problem, "V0", ["O2"])
    assert pool.add(problem, "V1", ["O1"])
    assert pool.add(problem, "V1", ["O2"])
    return problem, pool
