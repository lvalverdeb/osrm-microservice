"""E-03 (T-04) [GATE] — the independent verifier. SDD §11.2, INV-1..INV-9.

Every invariant gets a violating fixture that must be caught and a legal one
that must pass. A verifier that only ever sees good input proves nothing.

The independence is structural, not a promise: §11.2 requires the verifier to
share no code with any solver and to recompute from the raw sequences. The
import test below is what keeps that true as the package grows.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    Step,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

VERIFIER_SOURCE = Path("vrp/verify/verifier.py")


def problem(**overrides) -> Problem:
    locations = (
        Location(id="D", lat=9.94, lon=-84.05, matrix_index=0),
        Location(id="A", lat=9.95, lon=-84.06, matrix_index=1),
        Location(id="B", lat=9.96, lon=-84.07, matrix_index=2),
    )
    durations = ((0, 300, 800), (300, 0, 600), (900, 600, 0))
    distances = ((0, 5000, 12000), (5000, 0, 9000), (14000, 9000, 0))
    orders = (
        Order(id="OA", kind="JOB", quantities={"weight": 10},
              delivery=StopSpec(location_id="A",
                                time_windows=(TimeWindow(start=0, end=100000),),
                                service_fixed=120)),
        Order(id="OB", kind="JOB", quantities={"weight": 4},
              delivery=StopSpec(location_id="B",
                                time_windows=(TimeWindow(start=0, end=100000),),
                                service_fixed=60)),
    )
    vehicles = (Vehicle(id="V1", capacities={"weight": 50},
                        shift=TimeWindow(start=0, end=86400),
                        start_location_id="D", end_location_id="D",
                        **overrides),)
    return Problem(id="P", locations=locations, orders=orders, vehicles=vehicles,
                   matrix=TravelMatrix(version="m1", durations=durations,
                                       distances=distances))


def legal_solution() -> Solution:
    """Hand-built, not generated: the fixture must not depend on the evaluator."""
    steps = (
        Step(type="START", location_id="D", arrival=0, start_service=0, departure=0,
             load_after={"weight": 14}),
        Step(type="DELIVERY", location_id="A", order_id="OA",
             arrival=300, start_service=300, departure=420, load_after={"weight": 4}),
        Step(type="DELIVERY", location_id="B", order_id="OB",
             arrival=1020, start_service=1020, departure=1080, load_after={"weight": 0}),
        Step(type="END", location_id="D", arrival=1980, start_service=1980,
             departure=1980, load_after={"weight": 0}),
    )
    return Solution(problem_id="P", routes=(Route(vehicle_id="V1", steps=steps),),
                    objective_breakdown={"distance": 28000})


def codes(report) -> set[str]:
    return {violation.invariant for violation in report.violations}


# --- the verifier must accept what is legal ------------------------------

def test_a_legal_solution_passes_every_invariant():
    report = verify(problem(), legal_solution())
    assert report.ok, report.violations


# --- and reject what is not ----------------------------------------------

def test_inv1_catches_an_order_served_twice():
    solution = legal_solution()
    route = solution.routes[0]
    duplicated = replace(route, steps=route.steps + (route.steps[1],))
    report = verify(problem(), replace(solution, routes=(duplicated,)))
    assert "INV-1" in codes(report)


def test_inv1_catches_an_order_that_vanished():
    solution = legal_solution()
    route = solution.routes[0]
    without_b = replace(route, steps=tuple(s for s in route.steps if s.order_id != "OB"))
    report = verify(problem(), replace(solution, routes=(without_b,)))
    assert "INV-1" in codes(report)


def test_inv3_catches_service_starting_before_arrival():
    solution = legal_solution()
    route = solution.routes[0]
    broken = replace(route.steps[1], start_service=200)
    steps = route.steps[:1] + (broken,) + route.steps[2:]
    report = verify(problem(), replace(solution, routes=(replace(route, steps=steps),)))
    assert "INV-3" in codes(report)


def test_inv3_catches_service_outside_a_hard_window():
    narrow = problem()
    order = narrow.order("OA")
    tight = replace(order, delivery=replace(order.delivery,
                    time_windows=(TimeWindow(start=0, end=200),)))
    narrow = replace(narrow, orders=(tight, narrow.orders[1]))
    report = verify(narrow, legal_solution())      # serves A at 300, window shuts at 200
    assert "INV-3" in codes(report)


def test_inv4_catches_a_travel_time_that_does_not_match_the_matrix():
    solution = legal_solution()
    route = solution.routes[0]
    # Claim arrival at A 100s earlier than the pinned matrix allows.
    broken = replace(route.steps[1], arrival=200, start_service=200, departure=320)
    steps = route.steps[:1] + (broken,) + route.steps[2:]
    report = verify(problem(), replace(solution, routes=(replace(route, steps=steps),)))
    assert "INV-4" in codes(report)


def test_inv5_catches_a_load_above_capacity():
    report = verify(problem(max_duration=None), replace(
        legal_solution(),
        routes=(Route(vehicle_id="V1", steps=(
            replace(legal_solution().routes[0].steps[0], load_after={"weight": 999}),
        ) + legal_solution().routes[0].steps[1:]),)))
    assert "INV-5" in codes(report)


def test_inv5_catches_a_negative_load():
    solution = legal_solution()
    route = solution.routes[0]
    broken = replace(route.steps[2], load_after={"weight": -1})
    steps = route.steps[:2] + (broken,) + route.steps[3:]
    report = verify(problem(), replace(solution, routes=(replace(route, steps=steps),)))
    assert "INV-5" in codes(report)


def test_inv6_catches_a_route_longer_than_the_vehicle_allows():
    report = verify(problem(max_duration=600), legal_solution())   # route runs 1980s
    assert "INV-6" in codes(report)


def test_inv6_catches_a_route_leaving_the_shift_window():
    early = problem()
    vehicle = replace(early.vehicles[0], shift=TimeWindow(start=0, end=1000))
    report = verify(replace(early, vehicles=(vehicle,)), legal_solution())
    assert "INV-6" in codes(report)


def test_inv9_catches_objective_drift():
    """The invariant the SDD calls the most valuable test in the system."""
    solution = replace(legal_solution(), objective_breakdown={"distance": 27000})
    report = verify(problem(), solution)
    assert "INV-9" in codes(report)


# --- invariants that have no subject yet ---------------------------------

def test_invariants_with_no_subject_report_as_not_applicable():
    """This fixture declares no hours-of-service rules (INV-7), no locks
    (INV-8), no depot inventory (INV-13), no ride-time bounds (INV-14), no
    synchronisations (INV-15) and no release times (INV-17), so none of the six
    has a subject.

    A verifier that returned 'ok' for an invariant it cannot evaluate would be
    lying by omission, and the lie would survive until someone shipped an
    illegal duty timeline. The set grows as the model does -- INV-13 joined it
    with T-45, INV-14 with T-74, INV-15 with T-76 and INV-17 with T-104 --
    which is the point: an invariant added
    without a subject on this fixture must say so rather than quietly pass.
    """
    report = verify(problem(), legal_solution())
    assert report.not_applicable == {"INV-7", "INV-8", "INV-13", "INV-14",
                                     "INV-15", "INV-17"}
    assert report.ok is True


# --- independence is structural ------------------------------------------

def test_the_verifier_does_not_import_the_evaluator():
    """§11.2: no shared code with any solver, and never the move evaluator.

    Checked by reading the imports rather than by trusting a comment, so the
    boundary survives someone adding a convenient helper later.
    """
    tree = ast.parse(VERIFIER_SOURCE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {name for name in imported
                 if "evaluator" in name or "solver" in name
                 or name.endswith("hos.schedule")}
    assert not forbidden, f"verifier must not import {forbidden}"
    # `vrp.hos.rules` is permitted and `vrp.hos.schedule` is not, which is the
    # same distinction as the domain types: the regulation is reference data
    # both sides must share, the scheduler is the thing being judged. INV-7
    # asking the scheduler whether the scheduler was right would verify nothing.
    # Importing the domain types is allowed and expected: they are data, not
    # logic, and both sides must agree on what a Step is.
    assert any(name.startswith("vrp.model") or name == "vrp.model" for name in imported)


# --- INV-17: release times (FR-06) ---------------------------------------
# `FR-06` is a MUST -- "an order cannot depart before goods are available" --
# and the adapter passes `release_time` to PyVRP, so plans this engine builds
# respect it. Nothing checked it. §11.2 makes the verifier the gate precisely
# because it shares no code with any solver, so "the solver gets it right" is
# the one argument it may not accept: a second engine, a hand-built plan, or a
# plan from an integrator through `/verify` had nothing standing between a
# van leaving before its load existed and a report saying the plan was sound.
#
# Found from a delivery network whose envelopes are line-hauled to a depot
# overnight and delivered the next morning: "depot-bound envelopes are only
# dispatched from a depot after they have physically arrived there" is a
# release time, and it is the coupling between the two stages.


def released(when: int) -> Problem:
    """`OA` is not available until `when`; the route below leaves at 0."""
    base = problem()
    orders = (replace(base.orders[0], release_time=when), base.orders[1])
    return replace(base, orders=orders)


def test_inv17_catches_a_route_that_departs_before_its_load_exists():
    report = verify(released(5_000), legal_solution())

    assert "INV-17" in codes(report)
    assert not report.ok


def test_inv17_names_the_order_and_both_times():
    """A violation a dispatcher cannot act on is barely better than none."""
    report = verify(released(5_000), legal_solution())
    violation, = [v for v in report.violations if v.invariant == "INV-17"]

    assert violation.order_id == "OA"
    assert "5000" in violation.detail and "0" in violation.detail


def shifted(solution: Solution, delta: int) -> Solution:
    """The same plan, every clock moved by `delta`, so travel stays consistent
    and only the departure time changes."""
    routes = tuple(
        replace(route, steps=tuple(
            replace(step, arrival=step.arrival + delta,
                    start_service=step.start_service + delta,
                    departure=step.departure + delta)
            for step in route.steps))
        for route in solution.routes)
    return replace(solution, routes=routes)


def test_inv17_binds_on_the_departure_not_on_the_arrival():
    """The distinction the invariant is about, and the one a plausible reading
    gets wrong.

    The route leaves at 0 carrying goods released at 200 and reaches the stop
    at 300. Checking the *stop* would see 300 >= 200 and pass it -- a van that
    left before its load existed and was excused because it dawdled on the way.
    """
    report = verify(released(200), legal_solution())

    assert "INV-17" in codes(report)


def test_inv17_accepts_a_departure_exactly_at_the_release_time():
    """The bound is inclusive: goods available at 200 may leave at 200. An
    exclusive bound would refuse a plan that is precisely on time, which is the
    plan a good solver produces."""
    report = verify(released(200), shifted(legal_solution(), 200))

    assert "INV-17" not in codes(report)


def test_inv17_says_so_when_no_order_carries_a_release_time():
    """An invariant with no subject reports that rather than quietly passing,
    which is how `INV-14` and `INV-7` already behave."""
    report = verify(problem(), legal_solution())

    assert "INV-17" in report.not_applicable
    assert "INV-17" not in codes(report)
