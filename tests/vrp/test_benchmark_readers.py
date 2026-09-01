"""Public benchmark instances read into our model — §11.3, T-06, E-05.

The instances in `benchmarks/instances/` are real published benchmarks, taken
from PyVRP's test corpus:

    E-n22-k4         Christofides & Eilon CVRP, optimum 375 stated in the file
    X-n101-50-k13    Uchoa X-set CVRP
    RC208            Solomon VRPTW, with a reference solution alongside
    lrc206           Li & Lim PDPTW
    SmallVRPSPD      simultaneous pickup and delivery

Between them they cover four of §3.4's five named classes, which is what makes
this more than a parser test: a reader that mangles time windows still parses
E-n22-k4 perfectly, because E-n22-k4 has none.

The assertions that matter are not "it parsed". They are that the numbers
survive the crossing — that the optimum comes from the file rather than from
somebody's memory, that a matrix read back matches the coordinates it was
derived from, and that our own verifier accepts a solution the wider literature
already agreed was legal.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from vrp.benchmarks import Benchmark, gap_percent, read_benchmark
from vrp.verify import verify

INSTANCES = Path("benchmarks/instances")
ALL_FILES = sorted(p for p in INSTANCES.glob("*")
                   if p.suffix in (".vrp", ".txt", ".tsp"))

pytestmark = pytest.mark.skipif(
    not ALL_FILES, reason="benchmark instances not present")


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.stem)
def test_every_shipped_instance_reads_or_is_refused_by_name(path: Path):
    """All of them, so a reader tuned to one dialect cannot pass.

    "Reads" is not the only correct outcome. An instance using a feature this
    mapping has nowhere to put must be refused *and say which feature* -- what
    is forbidden is the third case, where the section is dropped and the file
    maps cleanly onto a different problem.
    """
    try:
        benchmark = read_benchmark(path)
    except NotImplementedError as refusal:
        assert len(str(refusal)) > 60, (
            f"{path.name} was refused without saying enough to act on")
        return

    assert isinstance(benchmark, Benchmark)
    assert benchmark.problem.orders, "an instance with no orders is not one"
    assert benchmark.problem.vehicles
    size = len(benchmark.problem.matrix.durations)
    assert size == len(benchmark.problem.locations)
    assert all(len(row) == size for row in benchmark.problem.matrix.distances)


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.stem)
def test_the_matrix_is_consistent_with_the_coordinates(path: Path):
    """A spot check that the matrix was not transposed or misaligned.

    EUC_2D means the arc cost is the rounded Euclidean distance between the two
    points, so this recomputes a few and compares. It catches the class of bug
    where an instance parses cleanly and describes a different problem.

    Reads the planar coordinates off the `Benchmark` rather than the
    `Location`s: benchmark points are not geographic and are deliberately not
    stored as latitudes.
    """
    import vrplib
    if str(vrplib.read_instance(str(path)).get("edge_weight_type")) != "EUC_2D":
        pytest.skip("arc costs are stated explicitly; any coordinates beside "
                    "them are decorative and need not agree with them")

    try:
        benchmark = read_benchmark(path)
    except NotImplementedError:
        pytest.skip("instance is refused by the mapping; see the sweep above")
    matrix = benchmark.problem.matrix
    points = benchmark.coordinates
    if not points:
        pytest.skip("instance carries no coordinates, only an explicit matrix")

    for i, j in ((0, 1), (1, 2), (0, len(points) - 1)):
        if i >= len(points) or j >= len(points):
            continue
        (ax, ay), (bx, by) = points[i], points[j]
        expected = math.hypot(ax - bx, ay - by)
        # VRPLIB rounds; allow a unit either way rather than pinning the
        # rounding rule, which differs between instance families.
        assert abs(matrix.distance(i, j) - expected) <= 1.5, (
            f"{path.stem}: d({i},{j})={matrix.distance(i, j)} "
            f"but the coordinates say {expected:.1f}")


def test_the_optimum_comes_from_the_file_not_from_memory():
    """E-n22-k4 states "Optimal value: 375" in its own COMMENT line.

    Reading it rather than transcribing it is the whole design: a hand-typed
    registry of best-known values is a registry of typos, and every gap
    computed against a wrong one is wrong while looking entirely fine.
    """
    benchmark = read_benchmark(INSTANCES / "E-n22-k4.txt")

    assert benchmark.best_known == 375
    assert benchmark.best_known_source == "instance COMMENT"


def test_a_reference_solution_supplies_the_cost_when_the_instance_does_not():
    """RC208 states no optimum, but ships a solution costing 776.1."""
    benchmark = read_benchmark(INSTANCES / "RC208.vrp")

    assert benchmark.best_known == 776
    assert benchmark.best_known_source == "RC208.sol"


def test_an_unknown_optimum_is_reported_as_unknown():
    """Not guessed, and not silently zero -- a zero best-known would make every
    gap infinite and every comparison meaningless."""
    benchmark = read_benchmark(INSTANCES / "SmallVRPSPD.vrp")

    assert benchmark.best_known is None
    assert benchmark.best_known_source == "unknown"


def test_solomon_time_windows_survive_the_crossing():
    """RC208 is a VRPTW: windows and service times are the point of it.

    A reader that dropped them would still parse, still solve, and produce
    plans that look plausible and ignore every customer's opening hours.
    """
    benchmark = read_benchmark(INSTANCES / "RC208.vrp")

    assert benchmark.kind == "CVRPTW"
    stops = [order.delivery for order in benchmark.problem.orders]
    assert all(stop.time_windows for stop in stops)
    assert any(stop.time_windows[0].start > 0 for stop in stops), \
        "every window opens at zero, which is not a time-window instance"
    assert all(stop.service_fixed == 10 for stop in stops), \
        "RC208 declares a service time of 10 for every customer"


def test_capacity_and_demand_survive_the_crossing():
    """E-n22-k4: capacity 6000, and demands that need four vehicles."""
    benchmark = read_benchmark(INSTANCES / "E-n22-k4.txt", vehicles=4)

    assert benchmark.vehicles_available == 4
    assert all(v.capacities["demand"] == 6000 for v in benchmark.problem.vehicles)
    total = sum(o.quantities["demand"] for o in benchmark.problem.orders)
    assert total > 6000 * 3, "should need more than three vehicles"
    assert total <= 6000 * 4, "and no more than the four the name declares"


def test_our_solver_produces_a_plan_our_verifier_accepts():
    """The point of the whole exercise: the same verifier that judges a Costa
    Rica round judges a published instance, and the plan is legal by its rules.

    No claim is made here about *quality* -- 200 iterations on a 22-stop CVRP
    is not a serious attempt at 375, and pretending otherwise is exactly the
    assertion §11.3 says to replace with evidence.
    """
    from vrp.solve.pyvrp_adapter import solve

    benchmark = read_benchmark(INSTANCES / "E-n22-k4.txt", vehicles=4)
    solution = solve(benchmark.problem, iterations=200, seed=0)

    report = verify(benchmark.problem, solution)
    assert report.ok, [str(v) for v in report.violations]
    assert not solution.unassigned


def test_the_gap_calculation_is_the_one_section_11_3_reports():
    assert gap_percent(375, 375) == 0.0
    assert gap_percent(final := 386, 375) == pytest.approx((final - 375) / 375 * 100)
    with pytest.raises(ValueError, match="positive"):
        gap_percent(100, 0)


# --------------------------------------------------------------------------
# The variants §13.3 asks for an anchor apiece — T-06's remainder
# --------------------------------------------------------------------------

def test_a_tsp_instance_reads_as_one_tour_over_every_city():
    """`CAT-VRP-003` §13.3 wants each variant section related to a public set,
    and §5 (TSP) had no anchor: the catalogue's largest new section in v2.0 was
    being measured against nothing.

    A TSPLIB file declares no capacity and no demands, which is not a defect in
    the file. The reader has to carry that through rather than inventing a
    limit nobody stated.
    """
    benchmark = read_benchmark(INSTANCES / "pr107.tsp")

    assert benchmark.kind == "TSP"
    assert len(benchmark.problem.orders) == 106, "107 cities, one of them the start"
    assert benchmark.vehicles_available == 1
    assert benchmark.best_known is None, (
        "pr107's optimum is famous and is not in the file; §11.3's rule is that "
        "it is read or it is absent, never transcribed")
    demanded = {q for order in benchmark.problem.orders
                for q in order.quantities.values()}
    assert demanded == {0}, "a TSP city demands nothing"


def test_a_multi_depot_instance_keeps_every_depot_a_depot():
    """The MDHVRPTW anchor. 29 catalogue scenarios are multi-depot, and the
    reader turned every depot but the first into a customer with no demand --
    an instance one stop larger than the one on disk, with a route starting in
    the wrong place."""
    benchmark = read_benchmark(INSTANCES / "OkSmallMultipleDepots.txt")
    problem = benchmark.problem

    depots = {vehicle.start_location_id for vehicle in problem.vehicles}
    assert len(depots) == 2, f"the file declares two depots; got {sorted(depots)}"
    assert len(problem.orders) == 3, "three customers, and two depots that are not"
    assert {v.start_location_id for v in problem.vehicles} == \
           {v.end_location_id for v in problem.vehicles}

    # VEHICLES_DEPOT_SECTION says which vehicle belongs where: 1 and 2 at the
    # first depot, 3 at the second.
    homes = [v.start_location_id for v in problem.vehicles]
    assert homes[0] == homes[1] != homes[2]


def test_a_site_dependent_instance_is_refused_by_name():
    """PR01 is site-dependent: `VEHICLES_ALLOWED_CLIENTS_SECTION` says which
    vehicle may serve which customer. Reading it and dropping that section
    would produce a plan that looks fine and answers a different question, so
    the mapping refuses and says which feature it refused."""
    with pytest.raises(NotImplementedError, match="allowed clients|site-dependent"):
        read_benchmark(INSTANCES / "PR01.vrp")


def test_the_tsp_anchor_is_solved_and_verified_as_one_tour():
    """§13.3: "Each variant section contributes at least one benchmark-
    comparable fixture so public benchmark performance and production
    performance can be related." §5's sixteen TSP scenarios had none.

    The optimum is not asserted here. `best_known` is None because pr107 does
    not state it, and asserting a number this file never carried would be the
    transcription §11.3 forbids -- see benchmarks/instances/README.md for the
    measured value and where the published one comes from.
    """
    from vrp.solve.pyvrp_adapter import solve

    benchmark = read_benchmark(INSTANCES / "pr107.tsp")
    solution = solve(benchmark.problem, iterations=2_000, seed=0)

    assert len([r for r in solution.routes
                if any(s.order_id for s in r.steps)]) == 1, "a TSP is one tour"
    visited = {s.order_id for r in solution.routes for s in r.steps if s.order_id}
    assert visited == {o.id for o in benchmark.problem.orders}
    assert verify(benchmark.problem, solution).ok


def test_the_multi_depot_anchor_starts_each_vehicle_at_its_own_depot():
    """The MDHVRPTW anchor solved rather than merely parsed: a reader that
    produces the right shape and a solver that ignores it would still leave
    29 catalogue scenarios unevidenced."""
    from vrp.solve.pyvrp_adapter import solve

    benchmark = read_benchmark(INSTANCES / "OkSmallMultipleDepots.txt")
    problem = benchmark.problem
    solution = solve(problem, iterations=2_000, seed=0)

    assert verify(problem, solution).ok
    for route in solution.routes:
        if not route.steps:
            continue
        home = problem.vehicle(route.vehicle_id).start_location_id
        assert route.steps[0].location_id == home
        assert route.steps[-1].location_id == home
