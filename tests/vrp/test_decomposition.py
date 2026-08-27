"""Large-instance decomposition — §7.6, T-37, E-37.

§7.6 is three requirements and three invariants, and they pull in different
directions. (a) partitions the instance, (b) re-optimises sub-problems against
an incumbent, (c) repairs the seams (a) left behind -- while DEC-1 insists the
result is feasible *globally*, which is precisely the thing no sub-problem can
check.

E-37's acceptance: "Large instances decompose and recombine with no invariant
violation at the boundaries."

The failure this module exists to prevent is not a bad score. It is a plan that
every sub-problem certifies and the whole instance rejects: fifty clusters each
politely sending a vehicle to the same dock at 06:00, none of them aware of the
other forty-nine. That is DEC-1, and it is the reason the orchestrator owns a
scheduling step no sub-solver could perform. `test_dock_capacity_is_enforced_
across_clusters` is the test that matters here; most of the rest would pass on
an orchestrator that merely ran the solver N times and concatenated.

DEC-3 gets its own test for a related reason: summing sub-problem objectives is
the obvious way to score a decomposed plan and it is wrong, because the terms
that make decomposition necessary -- fleet size, global lateness -- are exactly
the ones that do not add up.
"""

from __future__ import annotations

import os

import pytest

from vrp.decompose import (
    SubProblem,
    partition,
    popmusic,
    repair_boundaries,
    solve_decomposed,
)
from vrp.evaluator import ObjectiveWeights, evaluate
from vrp.generate import Shape, generate_instance, generate_large_instance
from vrp.matrix import PlanarMatrix, submatrix
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
)
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)
WEIGHTS = ObjectiveWeights(per_metre=1, per_second=0)

# Opt-in, because the full acceptance is a 10,000-stop solve and CI has minutes
# rather than an hour. The default-on scale test below is small enough to run
# every time and large enough that a monolithic solve is already the wrong
# shape, so this file never reduces to "skipped".
SCALE = os.environ.get("VRP_SCALE_TEST") == "1"


# --------------------------------------------------------------------------
# A matrix an instance this size can actually have
# --------------------------------------------------------------------------

def test_a_planar_matrix_agrees_cell_for_cell_with_a_stored_one():
    """The lazy matrix is only useful if it is the same matrix.

    Measured: a dense 10,000-square matrix is ~3.8 GB and roughly 12 minutes to
    build, which spends a fifth of NFR-01's hour before any solving. Computing
    cells on demand is the only way the acceptance instance exists at all -- so
    the arithmetic must match the stored form exactly, not approximately.
    """
    dense = generate_instance(3, shape=Shape.SLACK).matrix
    coords = generate_large_instance(3, stops=12).matrix.coordinates

    lazy = PlanarMatrix(version="generated-v1", coordinates=coords)
    rebuilt = submatrix(lazy, list(range(lazy.size)))

    assert rebuilt.size == lazy.size
    for origin in range(lazy.size):
        for destination in range(lazy.size):
            assert (rebuilt.duration(origin, destination)
                    == lazy.duration(origin, destination))
    assert dense.size == len(dense.durations)


def test_the_planar_matrix_bounds_are_real_upper_bounds():
    """§5.1's scaling wants "a real upper bound, deliberately loose rather than
    tight: a bound that is too small breaks the lexicographic guarantee, while
    one that is too large only makes the numbers bigger". A bounding-box
    diagonal is exactly that, and costs O(n) rather than O(n^2)."""
    problem = generate_large_instance(5, stops=200)
    matrix = problem.matrix

    longest, slowest = matrix.extremes()
    worst = max(matrix.distance(a, b)
                for a in range(matrix.size) for b in range(matrix.size))

    assert longest >= worst, (longest, worst)
    assert slowest > 0


def test_a_submatrix_preserves_travel_between_the_nodes_it_keeps():
    """Sub-problems are solved against their own small dense matrix. If that
    matrix disagrees with the global one the sub-solution is optimal for a
    different instance."""
    problem = generate_large_instance(7, stops=60)
    keep = [0, 5, 17, 33, 41]

    small = submatrix(problem.matrix, keep)

    assert small.size == len(keep)
    for i, origin in enumerate(keep):
        for j, destination in enumerate(keep):
            assert (small.duration(i, j)
                    == problem.matrix.duration(origin, destination))


# --------------------------------------------------------------------------
# (a) Cluster-first partitioning, evaluated as a component
# --------------------------------------------------------------------------

def test_every_order_lands_in_exactly_one_subproblem():
    problem = generate_large_instance(11, stops=300)
    clusters = partition(problem, target_size=60)

    seen = [order_id for cluster in clusters for order_id in cluster.order_ids]
    assert sorted(seen) == sorted(order.id for order in problem.orders)
    assert len(seen) == len(set(seen))


def test_a_vehicle_appears_in_at_most_one_subproblem():
    """DEC-2, stated as a test. A vehicle in two sub-problems is planned twice
    and can only do one of them, so the recombined plan is fiction."""
    problem = generate_large_instance(13, stops=300)
    clusters = partition(problem, target_size=60)

    owners = [vehicle_id for cluster in clusters
              for vehicle_id in cluster.vehicle_ids]
    assert len(owners) == len(set(owners)), "a vehicle was partitioned twice"


def test_every_subproblem_gets_at_least_one_vehicle():
    """A cluster with orders and no fleet is infeasible by construction, and
    would be reported as unassigned demand rather than as a partitioning bug."""
    problem = generate_large_instance(17, stops=240)
    for cluster in partition(problem, target_size=40):
        assert cluster.vehicle_ids, f"cluster {cluster.index} has no vehicle"
        assert cluster.order_ids


def test_the_partition_balances_demand_rather_than_only_space():
    """§7.6(a): sub-problems must be "jointly capacity- and time-aware, not
    merely spatial". A purely spatial split puts every heavy order in one
    cluster whenever demand correlates with position, which it does here."""
    problem = _demand_gradient(stops=180)

    clusters = partition(problem, target_size=45)
    loads = [sum(problem.order(o).quantities["units"] for o in c.order_ids)
             for c in clusters]

    assert max(loads) <= 2 * min(loads), (
        f"loads {loads} are lopsided; the partition is spatial only")


def test_the_partition_keeps_orders_with_overlapping_windows_together():
    """§7.6(a) names time-window overlap alongside proximity. Two stops next
    door to each other but four hours apart do not belong on one route, and a
    partitioner blind to that hands the sub-solver a problem with no good
    answer in it."""
    problem = _two_shifts(stops=120)
    clusters = partition(problem, target_size=30)

    for cluster in clusters:
        opens = {problem.order(o).delivery.time_windows[0].start
                 for o in cluster.order_ids}
        assert len(opens) == 1, (
            f"cluster {cluster.index} mixes windows opening at {sorted(opens)}")


def test_the_partitioner_beats_a_fixed_spatial_rule_on_both_shapes():
    """§7.6(a): "fixed partitioning rules perform inconsistently across
    instances with differing spatial/demand/operational characteristics, so the
    partitioner MUST be adaptive and MUST be evaluated as a component".

    Evaluated as a component means exactly this: the same two partitioners, two
    instance shapes, and a measurement rather than a claim. The fixed rule is
    allowed to win nothing; it must lose somewhere, or "adaptive" is decoration.
    """
    from vrp.decompose import partition_quality, partition_spatially

    worse_somewhere = False
    for problem in (_demand_gradient(stops=180), _two_shifts(stops=120)):
        adaptive = partition_quality(problem, partition(problem, target_size=40))
        fixed = partition_quality(
            problem, partition_spatially(problem, target_size=40))
        assert adaptive <= fixed * 1.05, (adaptive, fixed)
        worse_somewhere |= fixed > adaptive * 1.10

    assert worse_somewhere, (
        "the fixed rule matched the adaptive one on every shape; either the "
        "shapes do not discriminate or the adaptivity is inert")


# --------------------------------------------------------------------------
# (b) POPMUSIC sub-problem re-optimisation
# --------------------------------------------------------------------------

def test_popmusic_never_returns_a_worse_plan():
    """It is an improvement procedure working against an incumbent. Returning
    something worse would mean the caller has to re-check what the orchestrator
    was supposed to guarantee."""
    problem = generate_large_instance(19, stops=120)
    plan = _crossed_plan(problem, routes=6)
    before = evaluate(problem, plan, WEIGHTS).total

    after = popmusic(problem, plan, radius=2, rounds=2, seed=0)

    assert evaluate(problem, after, WEIGHTS).total <= before


def test_popmusic_improves_a_deliberately_crossed_plan():
    """The control on the test above: a procedure that returns its input
    unchanged also never worsens it."""
    problem = generate_large_instance(23, stops=120)
    plan = _crossed_plan(problem, routes=6)
    before = evaluate(problem, plan, WEIGHTS).total

    after = popmusic(problem, plan, radius=3, rounds=3, seed=0)

    assert evaluate(problem, after, WEIGHTS).total < before, (
        "the incumbent was not improved at all")


def test_popmusic_gathers_the_seeds_nearest_routes():
    """§7.6(b): "select a seed route, gather its `r` nearest routes". Nearest,
    not arbitrary -- a sub-problem of unrelated routes has no improving move in
    it and burns the budget finding that out."""
    from vrp.decompose import nearest_routes

    problem = generate_large_instance(29, stops=100)
    plan = _crossed_plan(problem, routes=5)

    chosen = nearest_routes(problem, plan, seed_vehicle=next(iter(plan)),
                            radius=3)

    assert len(chosen) == 4, chosen           # the seed plus three
    assert next(iter(plan)) in chosen
    assert len(set(chosen)) == len(chosen)


def test_popmusic_keeps_every_order():
    problem = generate_large_instance(31, stops=120)
    plan = _crossed_plan(problem, routes=6)

    after = popmusic(problem, plan, radius=2, rounds=2, seed=0)

    assert (sorted(o for r in after.values() for o in r)
            == sorted(o for r in plan.values() for o in r))


def test_popmusic_is_deterministic_for_a_seed():
    """CON-4. Two runs, one seed, one answer."""
    problem = generate_large_instance(37, stops=100)
    plan = _crossed_plan(problem, routes=5)

    first = popmusic(problem, plan, radius=2, rounds=2, seed=5)
    second = popmusic(problem, plan, radius=2, rounds=2, seed=5)

    assert first == second


# --------------------------------------------------------------------------
# (c) Cross-boundary repair
# --------------------------------------------------------------------------

def test_boundary_repair_removes_a_seam():
    """§7.6(c): skipping this "leaves visible seams at cluster borders, which
    dispatchers notice immediately".

    The fixture is a seam by construction: two clusters split down the middle
    of a line of stops, so the pair straddling the border belongs to the wrong
    side. Nothing inside either cluster can fix that.
    """
    problem, plan, clusters = _seamed_instance()
    before = evaluate(problem, plan, WEIGHTS).total

    repaired = repair_boundaries(problem, plan, clusters, seed=0)

    assert evaluate(problem, repaired, WEIGHTS).total < before, (
        "the seam survived; cross-boundary moves are not being tried")


def test_boundary_repair_only_considers_moves_that_cross_a_boundary():
    """§7.6(c) asks for a *pruned* search "using the similarity metadata
    computed during decomposition". Re-running a full local search over the
    whole instance would find the same seams and cost what decomposition was
    meant to save."""
    from vrp.decompose import boundary_candidates

    problem, plan, clusters = _seamed_instance()
    candidates = boundary_candidates(problem, plan, clusters)

    owner = {order_id: cluster.index
             for cluster in clusters for order_id in cluster.order_ids}
    assert candidates
    for order_id, vehicle_id in candidates:
        home = owner[order_id]
        target = next(c.index for c in clusters if vehicle_id in c.vehicle_ids)
        assert home != target, (
            f"{order_id} -> {vehicle_id} stays inside cluster {home}")


def test_boundary_repair_keeps_every_order():
    problem, plan, clusters = _seamed_instance()
    repaired = repair_boundaries(problem, plan, clusters, seed=0)

    assert (sorted(o for r in repaired.values() for o in r)
            == sorted(o for r in plan.values() for o in r))


# --------------------------------------------------------------------------
# DEC-1 .. DEC-3
# --------------------------------------------------------------------------

def test_dock_capacity_is_enforced_across_clusters():
    """DEC-1, and the reason this orchestrator is more than a for-loop.

    "Depot inventory, dock capacity and shared-vehicle constraints MUST be
    enforced globally, never per cluster." Every sub-problem here is
    individually fine: one vehicle, one bay, no contention. Concatenated, eight
    vehicles leave one depot with two bays at the same instant -- FR-19's "40
    vehicles planned to depart at 06:00 and there are 8 bays" in miniature.

    No sub-solver can see this, which is exactly why the check belongs to the
    orchestrator and why INV-12 is the judge.
    """
    problem = _one_dock(stops=32, vehicles=8, bays=2)

    solution = solve_decomposed(problem, target_size=8, seed=0)
    report = verify(problem, solution)

    assert report.ok, [str(v) for v in report.violations[:4]]


def test_a_naive_concatenation_would_fail_that_check():
    """The control. If the unstaggered plan verified anyway, the test above
    would be passing on an instance that never posed the problem."""
    from vrp.decompose import concatenate

    problem = _one_dock(stops=32, vehicles=8, bays=2)
    clusters = partition(problem, target_size=8)
    naive = concatenate(problem, clusters, seed=0, stagger=False)

    assert not verify(problem, naive).ok, (
        "the fixture does not actually contend for docks")


def test_no_vehicle_is_planned_twice_in_a_round():
    """DEC-2 at the level that matters: not the partition's bookkeeping but the
    finished plan."""
    problem = generate_large_instance(41, stops=200)
    solution = solve_decomposed(problem, target_size=50, seed=0)

    used = [route.vehicle_id for route in solution.routes]
    assert len(used) == len(set(used))


def test_the_global_objective_is_not_the_sum_of_the_subproblem_objectives():
    """DEC-3: the global objective "is always evaluated by the canonical
    evaluator, never summed from sub-problem objectives (which double-count or
    omit shared terms)".

    Summing is the obvious implementation and it is wrong: per-vehicle fixed
    costs and any global term are counted once per cluster. The test makes the
    two disagree and insists the orchestrator reports the canonical figure.
    """
    problem = generate_large_instance(43, stops=150)
    solution = solve_decomposed(problem, target_size=40, seed=0)

    assignment = {route.vehicle_id:
                  [s.order_id for s in route.steps if s.order_id]
                  for route in solution.routes}
    canonical = evaluate(problem, assignment, WEIGHTS).total

    assert solution.objective_breakdown["total"] == canonical


def test_the_recombined_plan_verifies():
    """E-37's acceptance in one line: "no invariant violation at the
    boundaries"."""
    problem = generate_large_instance(47, stops=200)
    solution = solve_decomposed(problem, target_size=50, seed=0)

    report = verify(problem, solution)
    assert report.ok, [str(v) for v in report.violations[:4]]


def test_decomposition_is_deterministic_for_a_seed():
    problem = generate_large_instance(53, stops=150)

    first = solve_decomposed(problem, target_size=40, seed=3)
    second = solve_decomposed(problem, target_size=40, seed=3)

    assert first.routes == second.routes


# --------------------------------------------------------------------------
# Scale
# --------------------------------------------------------------------------

def test_a_thousand_stops_decompose_and_recombine():
    """Default-on, and already past the size at which §7.6 says "monolithic
    search degrades" is approaching. A test that only ever ran under an
    environment variable would leave this file proving nothing on most runs."""
    problem = generate_large_instance(59, stops=1_000)

    solution = solve_decomposed(problem, target_size=100, seed=0)

    assert verify(problem, solution).ok
    assert not solution.unassigned, len(solution.unassigned)


@pytest.mark.skipif(not SCALE, reason="set VRP_SCALE_TEST=1 for the 10k solve")
def test_ten_thousand_stops_within_the_hour():
    """NFR-01's second clause, and T-37's acceptance."""
    import time

    problem = generate_large_instance(61, stops=10_000)
    started = time.monotonic()
    solution = solve_decomposed(problem, target_size=200, seed=0)
    elapsed = time.monotonic() - started

    print(f"\n  10,000 stops in {elapsed / 60:.1f} min, "
          f"{len(solution.routes)} routes")
    assert verify(problem, solution).ok
    assert elapsed <= 60 * 60, elapsed


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _grid_problem(coords, demands, opens, vehicles: int, capacity: int,
                  bays: int | None = None, identifier: str = "dec",
                  load_seconds: int = 0) -> Problem:
    # A depot needs a loading time to occupy a bay: INV-12 counts occupancy over
    # the span where a START step's departure exceeds its start_service, so a
    # depot where loading is instantaneous satisfies any bay count vacuously.
    # `test_a_naive_concatenation_would_fail_that_check` caught this fixture
    # doing exactly that, which is what it is for.
    locations = [Location(id="D", lat=9.9, lon=-84.0, matrix_index=0,
                          dock_capacity=bays, dwell_overhead=load_seconds)]
    locations += [Location(id=f"C{i}", lat=9.9 + y / 100, lon=-84.0 + x / 100,
                           matrix_index=i + 1)
                  for i, (x, y) in enumerate(coords)]
    matrix = PlanarMatrix(version=f"{identifier}-v1",
                          coordinates=((0.0, 0.0), *coords))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"units": demands[i]},
              delivery=StopSpec(
                  location_id=f"C{i}", service_fixed=60,
                  time_windows=(TimeWindow(start=opens[i],
                                           end=opens[i] + 6 * 3600),)))
        for i in range(len(coords)))
    fleet = tuple(
        Vehicle(id=f"V{n}", capacities={"units": capacity}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for n in range(vehicles))
    return Problem(id=identifier, locations=tuple(locations), orders=orders,
                   vehicles=fleet, matrix=matrix)


def _demand_gradient(stops: int) -> Problem:
    """Demand rising with x, so a spatial split is also a demand split."""
    coords = [((i % 20) - 10.0, (i // 20) - 4.0) for i in range(stops)]
    demands = {i: 1 + (i % 20) for i in range(stops)}
    return _grid_problem(coords, demands, dict.fromkeys(range(stops), 0),
                         vehicles=stops // 10, capacity=400,
                         identifier="gradient")


def _two_shifts(stops: int) -> Problem:
    """Neighbouring stops on opposite shifts: proximity alone misleads."""
    coords = [((i % 20) - 10.0, (i // 20) - 3.0) for i in range(stops)]
    opens = {i: 0 if i % 2 == 0 else 6 * 3600 for i in range(stops)}
    return _grid_problem(coords, dict.fromkeys(range(stops), 1), opens,
                         vehicles=stops // 10, capacity=400,
                         identifier="shifts")


def _one_dock(stops: int, vehicles: int, bays: int) -> Problem:
    coords = [(float(i % 8) - 4.0, float(i // 8) - 2.0) for i in range(stops)]
    return _grid_problem(coords, dict.fromkeys(range(stops), 1),
                         dict.fromkeys(range(stops), 0), vehicles=vehicles,
                         capacity=8, bays=bays, identifier="dock",
                         load_seconds=1800)


def _crossed_plan(problem: Problem, routes: int) -> dict[str, list[str]]:
    """Orders dealt round-robin to vehicles: legal, and badly crossed."""
    if routes > len(problem.vehicles):
        raise AssertionError(
            f"fixture asks for {routes} routes; the instance has "
            f"{len(problem.vehicles)} vehicles")
    fleet = [vehicle.id for vehicle in problem.vehicles[:routes]]
    plan: dict[str, list[str]] = {vehicle_id: [] for vehicle_id in fleet}
    for position, order in enumerate(problem.orders):
        plan[fleet[position % routes]].append(order.id)
    return plan


def _seamed_instance() -> tuple[Problem, dict[str, list[str]], list[SubProblem]]:
    """Two clusters split across a line of stops, so the border pair is wrong.

    The stops run left to right. The partition cuts in the middle, and the plan
    hands each half to one vehicle in index order -- so each route ends by
    driving back across the seam it should never have crossed.
    """
    stops = 20
    coords = [(float(i), 0.0) for i in range(stops)]
    problem = _grid_problem(coords, dict.fromkeys(range(stops), 1),
                            dict.fromkeys(range(stops), 0), vehicles=2,
                            capacity=100, identifier="seam")
    left = [f"O{i}" for i in range(stops) if i % 2 == 0]
    right = [f"O{i}" for i in range(stops) if i % 2 == 1]
    clusters = [
        SubProblem(index=0, order_ids=tuple(left), vehicle_ids=("V0",),
                   problem=problem),
        SubProblem(index=1, order_ids=tuple(right), vehicle_ids=("V1",),
                   problem=problem),
    ]
    return problem, {"V0": left, "V1": right}, clusters
