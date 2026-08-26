"""Two engines, one domain model — CON-3, §7.3, T-30, E-30.

CON-3: "The domain model is defined independently of any solver. Solvers are
pluggable adapters behind a stable internal problem representation. No
solver-specific concept (an OR-Tools dimension, a PyVRP `ProblemData`, a VROOM
job) may leak into the domain layer."

One adapter cannot demonstrate that claim. However clean it looks, a `Problem`
shaped around PyVRP's assumptions would still compile perfectly — the leak
would be invisible because nothing else ever reads the model. Two engines make
it falsifiable: the same `Problem` object, untouched, handed to both.

The tests that matter are not "both produce a plan". They are that the *same
verifier* accepts both, and the *same evaluator* scores them on one scale. If
either engine needed its own verifier or its own cost function, the domain
model would not be the thing both were solving.

No claim is made that the two agree on the *answer*. They are different
algorithms and will find different routes; §7.3 keeps OR-Tools for
expressiveness rather than for quality. Asserting equal plans would be
asserting something false.
"""

from __future__ import annotations

import pytest

from vrp.evaluator import ObjectiveWeights, evaluate
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
from vrp.solve import pyvrp_adapter
from vrp.verify import verify

ortools_adapter = pytest.importorskip("vrp.solve.ortools_adapter",
                                      reason="ortools not installed")

DAY = TimeWindow(start=0, end=12 * 3600)


def simple(stops: int = 5, capacity: int = 100, vans: int = 2,
           windows: bool = False) -> Problem:
    """A CVRP(TW) both adapters can express, so the comparison is about them."""
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * 600 for j in range(size)) for i in range(size))

    orders = []
    for i in range(1, size):
        window = (TimeWindow(start=3600 * (i % 3), end=3600 * (i % 3) + 7200)
                  if windows else DAY)
        orders.append(Order(id=f"O{i}", kind="JOB", quantities={"kg": 10},
                            delivery=StopSpec(location_id=f"C{i}",
                                              time_windows=(window,),
                                              service_fixed=120)))
    fleet = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": capacity}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for n in range(1, vans + 1))
    return Problem(id="agree", locations=locations, orders=tuple(orders),
                   vehicles=fleet,
                   matrix=TravelMatrix(version="ag", durations=grid,
                                       distances=grid))


def assignment_of(solution) -> dict[str, list[str]]:
    return {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
            for route in solution.routes}


# --------------------------------------------------------------------------
# CON-3: one model, two engines
# --------------------------------------------------------------------------

def test_both_engines_consume_the_same_problem_object():
    """The claim, stated as directly as it can be: one `Problem`, no adaptation
    between the two calls, no engine-shaped fields on it."""
    problem = simple()

    from_pyvrp = pyvrp_adapter.solve(problem, iterations=400, seed=0)
    from_ortools = ortools_adapter.solve(problem, solutions=200, seed=0)

    assert from_pyvrp.problem_id == from_ortools.problem_id == problem.id
    assert not from_pyvrp.unassigned
    assert not from_ortools.unassigned


def test_the_same_verifier_accepts_both():
    """If OR-Tools needed its own verifier, the domain model would not be the
    thing both engines were solving -- it would be two models that resemble
    each other."""
    problem = simple()

    for name, solution in (
        ("pyvrp", pyvrp_adapter.solve(problem, iterations=400, seed=0)),
        ("ortools", ortools_adapter.solve(problem, solutions=200, seed=0)),
    ):
        report = verify(problem, solution)
        assert report.ok, (name, [str(v) for v in report.violations])


def test_the_same_evaluator_scores_both_on_one_scale():
    """E-30's acceptance. Two plans, one cost function, comparable numbers.

    The costs are *not* asserted equal: they are different algorithms and will
    find different routes. What must hold is that both are finite, positive and
    produced by the same evaluator -- a shared scale is what makes a portfolio
    able to choose between engines at all.
    """
    problem = simple()
    weights = ObjectiveWeights(per_metre=1, per_second=0)

    scores = {}
    for name, solution in (
        ("pyvrp", pyvrp_adapter.solve(problem, iterations=400, seed=0)),
        ("ortools", ortools_adapter.solve(problem, solutions=200, seed=0)),
    ):
        scores[name] = evaluate(problem, assignment_of(solution), weights).total

    assert all(score > 0 for score in scores.values()), scores
    # Same order of magnitude: wildly different totals would mean one engine
    # was being scored against a different instance.
    ratio = max(scores.values()) / min(scores.values())
    assert ratio < 3, scores


def test_both_engines_respect_capacity():
    """A constraint the domain states once and both engines must honour."""
    problem = simple(stops=6, capacity=20, vans=3)

    for name, solution in (
        ("pyvrp", pyvrp_adapter.solve(problem, iterations=600, seed=0)),
        ("ortools", ortools_adapter.solve(problem, solutions=300, seed=0)),
    ):
        report = verify(problem, solution)
        assert not [v for v in report.violations if v.invariant == "INV-5"], name


def test_both_engines_respect_time_windows():
    problem = simple(stops=5, windows=True)

    for name, solution in (
        ("pyvrp", pyvrp_adapter.solve(problem, iterations=600, seed=0)),
        ("ortools", ortools_adapter.solve(problem, solutions=300, seed=0)),
    ):
        report = verify(problem, solution)
        assert not [v for v in report.violations if v.invariant == "INV-3"], (
            name, [str(v) for v in report.violations])


def test_the_engines_are_recorded_distinctly():
    """CON-4's replay record must name which engine produced the plan, or a
    replay reproduces a different algorithm's answer."""
    problem = simple()

    assert pyvrp_adapter.solve(problem, iterations=200,
                               seed=0).solver["solver"].startswith("pyvrp")
    assert ortools_adapter.solve(problem, solutions=100,
                                 seed=0).solver["solver"] == "ortools"


# --------------------------------------------------------------------------
# Scope, refused rather than approximated
# --------------------------------------------------------------------------

@pytest.mark.parametrize("what", ["shipment", "multi_window", "locks"])
def test_the_escape_hatch_declines_what_it_cannot_model(what):
    """An adapter that quietly ignored a constraint would return a plan that
    looks like an answer. §7.3 keeps OR-Tools for expressiveness; an adapter
    narrower than the library is a statement about this adapter's scope, and it
    says so by name.
    """
    problem = simple(stops=3)
    if what == "shipment":
        order = Order(id="S1", kind="SHIPMENT", quantities={"kg": 1},
                      pickup=StopSpec(location_id="C1", time_windows=(DAY,),
                                      service_fixed=60),
                      delivery=StopSpec(location_id="C2", time_windows=(DAY,),
                                        service_fixed=60))
        problem = Problem(id=problem.id, locations=problem.locations,
                          orders=(order,), vehicles=problem.vehicles,
                          matrix=problem.matrix)
    elif what == "multi_window":
        first = problem.orders[0]
        widened = Order(
            id=first.id, kind="JOB", quantities=first.quantities,
            delivery=StopSpec(location_id=first.delivery.location_id,
                              time_windows=(TimeWindow(0, 3600),
                                            TimeWindow(7200, 10800)),
                              service_fixed=60))
        problem = Problem(id=problem.id, locations=problem.locations,
                          orders=(widened, *problem.orders[1:]),
                          vehicles=problem.vehicles, matrix=problem.matrix)
    else:
        from vrp.model import Lock
        problem = Problem(id=problem.id, locations=problem.locations,
                          orders=problem.orders, vehicles=problem.vehicles,
                          matrix=problem.matrix,
                          locks=(Lock(kind="FORCE_DEPLOY", vehicle_id="V1"),))

    with pytest.raises(NotImplementedError, match="PyVRP adapter"):
        ortools_adapter.solve(problem, solutions=50, seed=0)


def test_a_generated_instance_is_solved_by_both():
    """Against the E-04 generator rather than a hand-built fixture, so the
    agreement is not an artefact of one carefully shaped instance."""
    problem = generate_instance(31, shape=Shape.SLACK)

    from_pyvrp = pyvrp_adapter.solve(problem, iterations=400, seed=0)
    from_ortools = ortools_adapter.solve(problem, solutions=200, seed=0)

    assert verify(problem, from_pyvrp).ok
    assert verify(problem, from_ortools).ok
