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
                   if p.suffix in (".vrp", ".txt"))

pytestmark = pytest.mark.skipif(
    not ALL_FILES, reason="benchmark instances not present")


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.stem)
def test_every_shipped_instance_reads(path: Path):
    """All of them, so a reader tuned to one dialect cannot pass."""
    benchmark = read_benchmark(path)

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
    benchmark = read_benchmark(path)
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
