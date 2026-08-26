"""E-12 (T-12) — PyVRP adapter: model compiler and solution mapper.

SDD §7.3 lists PyVRP first for CVRPTW. This is the first end-to-end solve in the
repository, and its acceptance is not "a plan came back" but **the independent
verifier accepts the plan the solver produced**.

That distinction is the whole design. The mapped solution carries PyVRP's *own*
arrival and service times, not times recomputed by our evaluator, so the
verifier checking INV-3 and INV-4 against the pinned matrix is genuinely
checking the solver's arithmetic rather than agreeing with itself.

The instance here is Solomon-shaped -- clustered customers, a depot window, unit
capacity pressure -- but built in the domain model. Reading real Solomon files
is `E-05`/`T-06`, and claiming benchmark coverage this does not have would be
worse than not claiming it.
"""

from __future__ import annotations

import pytest

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

pyvrp = pytest.importorskip("pyvrp", reason="solver extra not installed")
from vrp.solve.pyvrp_adapter import solve

DAY = TimeWindow(start=0, end=12 * 3600)


def cvrptw(vehicles: int = 3, capacity: int = 40,
           windows: bool = True) -> Problem:
    """Three clusters around a depot, eight customers, integer everything."""
    coords = [(0, 0),
              (2, 1), (2, 2), (3, 1),         # east cluster
              (-2, 1), (-3, 2),               # west cluster
              (0, 4), (1, 4), (-1, 4)]        # north cluster
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + y / 100,
                 lon=-84.0 + x / 100, matrix_index=i)
        for i, (x, y) in enumerate(coords)
    )

    def leg(a: int, b: int) -> tuple[int, int]:
        (ax, ay), (bx, by) = coords[a], coords[b]
        metres = round((abs(ax - bx) + abs(ay - by)) * 1000)
        return metres, metres * 2          # 0.5 m/s, so times are legible

    size = len(coords)
    durations = tuple(tuple(0 if i == j else leg(i, j)[1] for j in range(size))
                      for i in range(size))
    distances = tuple(tuple(0 if i == j else leg(i, j)[0] for j in range(size))
                      for i in range(size))

    # Deliberately tight enough that a naive order breaks them.
    spans = {1: (0, 20000), 2: (0, 20000), 3: (0, 20000),
             4: (10000, 30000), 5: (10000, 30000),
             6: (20000, 43200), 7: (20000, 43200), 8: (20000, 43200)}
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"units": 6},
              delivery=StopSpec(
                  location_id=f"C{i}",
                  time_windows=(TimeWindow(*spans[i]),) if windows else (DAY,),
                  service_fixed=300))
        for i in range(1, size)
    )
    fleet = tuple(
        Vehicle(id=f"V{v}", capacities={"units": capacity}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for v in range(1, vehicles + 1)
    )
    return Problem(id="cvrptw", locations=locations, orders=orders,
                   vehicles=fleet,
                   matrix=TravelMatrix(version="euclid", durations=durations,
                                       distances=distances))


def test_the_verifier_accepts_the_plan_the_solver_produced():
    """The acceptance for E-12. Everything else here is detail."""
    problem = cvrptw()
    solution = solve(problem, iterations=300, seed=42)
    report = verify(problem, solution)
    assert report.ok, [str(v) for v in report.violations]


def test_every_order_is_placed_when_capacity_allows():
    problem = cvrptw()
    solution = solve(problem, iterations=300, seed=42)
    served = {step.order_id for route in solution.routes
              for step in route.steps if step.order_id}
    assert served == {order.id for order in problem.orders}
    assert solution.unassigned == ()


def test_time_windows_are_honoured():
    """The verifier enforces this, so a passing report is the assertion.

    Checked separately against a windowless variant: if the windowed instance
    produced the same routes, the windows would not be constraining anything
    and the test above would prove nothing.
    """
    windowed = solve(cvrptw(windows=True), iterations=300, seed=42)
    plain = solve(cvrptw(windows=False), iterations=300, seed=42)

    def sequence(solution):
        return [tuple(s.order_id for s in r.steps if s.order_id)
                for r in solution.routes]

    assert verify(cvrptw(windows=True), windowed).ok
    assert sequence(windowed) != sequence(plain), \
        "windows did not change the plan, so they were not binding"


def test_capacity_pressure_forces_more_vehicles():
    """48 units of demand across vehicles that hold 20 needs at least three."""
    problem = cvrptw(vehicles=3, capacity=20)
    solution = solve(problem, iterations=300, seed=42)
    assert verify(problem, solution).ok
    assert len(solution.routes) >= 3


def test_an_impossible_instance_is_labelled_infeasible_not_feasible():
    """One vehicle, four times too little capacity.

    Every client is required here -- dropping work needs optional orders with
    prizes, which is `T-27` -- so PyVRP returns a best-effort plan that breaks
    capacity rather than leaving orders unassigned. The adapter must report the
    solver's own verdict.

    This test exists because the first version of the mapper hardcoded
    `FEASIBLE`, and the verifier is what caught it: `INV-5 load units=48
    exceeds capacity 12`. The lesson is in the assertion pair below — the
    status must be honest *and* the verifier must still object.
    """
    problem = cvrptw(vehicles=1, capacity=12)
    solution = solve(problem, iterations=300, seed=42)
    assert solution.status == "INFEASIBLE"
    report = verify(problem, solution)
    assert not report.ok
    assert "INV-5" in {v.invariant for v in report.violations}


def test_the_same_seed_gives_the_same_plan():
    """CON-4. A solver that drifts run to run cannot be regression-tested."""
    problem = cvrptw()
    runs = {
        tuple(tuple(s.order_id for s in r.steps) for r in solve(
            problem, iterations=200, seed=7).routes)
        for _ in range(3)
    }
    assert len(runs) == 1
