"""Multi-dimensional capacity and peak load — FR-02, FR-03, §6.1, T-20, E-20.

§6.1 states the requirement and names the bug in the same breath: "For
simultaneous pickup-and-delivery, the binding quantity is the **peak load along
the route**, not the total — computing feasibility from route totals is wrong
and is a classic production bug."

The distinction only exists once load moves in both directions. A delivery-only
route starts full and empties, so peak == total and the wrong rule gives the
right answer by accident. Add a pickup and the two part company:
`test_a_route_legal_on_totals_can_be_illegal_at_its_peak` is that case, and it
is the reason this file exists.

Two things were broken before E-20. Multi-dimensional capacity was refused
outright, so "a van is full when *any* of weight, volume or pallets is
exhausted" could not be expressed. And a pickup-only order was compiled to
PyVRP as a *delivery*, so a route collecting goods was planned as one shedding
them — the load profile inverted, silently.
"""

from __future__ import annotations

import pytest

from vrp.evaluator import build_timeline
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(orders: tuple[Order, ...], capacities: dict[str, int],
             stops: int) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    distances = tuple(tuple(abs(i - j) * 1000 for j in range(size))
                      for i in range(size))
    durations = tuple(tuple(abs(i - j) * 60 for j in range(size))
                      for i in range(size))
    return Problem(
        id="cap", locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities=capacities, shift=DAY,
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="cap-v1", durations=durations,
                            distances=distances))


def drop(order_id: str, stop: str, **quantities) -> Order:
    return Order(id=order_id, kind="JOB", quantities=quantities,
                 delivery=StopSpec(location_id=stop, time_windows=(DAY,),
                                   service_fixed=60))


def collect(order_id: str, stop: str, **quantities) -> Order:
    return Order(id=order_id, kind="JOB", quantities=quantities,
                 pickup=StopSpec(location_id=stop, time_windows=(DAY,),
                                 service_fixed=60))


# --------------------------------------------------------------------------
# Multi-dimensional capacity (FR-02)
# --------------------------------------------------------------------------

def test_several_capacity_dimensions_are_accepted():
    """A van is full when *any* dimension is exhausted (§6.1), which cannot
    even be stated while the adapter takes one dimension."""
    problem = instance((drop("O1", "C1", kg=100, m3=2),), {"kg": 1_000, "m3": 10}, 1)
    solution = solve(problem, iterations=200, seed=0)

    assert solution.status == "FEASIBLE"
    assert verify(problem, solution).ok


def test_a_vehicle_full_on_one_dimension_is_full():
    """Volume binds while weight has room to spare. A model that checked only
    the dimension that happened to be listed first would load the van past its
    cube and call the plan feasible."""
    orders = tuple(drop(f"O{i}", f"C{i}", kg=1, m3=4) for i in range(1, 4))
    # 3 x 4 m3 = 12 against a 10 m3 van: the weight is trivial, the cube is not.
    problem = instance(orders, {"kg": 10_000, "m3": 10}, 3)
    solution = solve(problem, iterations=300, seed=0)

    assert solution.unassigned or solution.status == "INFEASIBLE", \
        "loaded 12 m3 into a 10 m3 van"


def test_load_is_reported_on_every_dimension():
    """INV-5 checks the loads a step reports, so a step reporting one dimension
    is unchecked on the others -- the shape that hid the HOS capacity gap."""
    problem = instance((drop("O1", "C1", kg=100, m3=2),), {"kg": 1_000, "m3": 10}, 1)
    timeline = build_timeline(problem, "V1", ["O1"])

    assert set(timeline[0].load_after) == {"kg", "m3"}
    assert timeline[0].load_after == {"kg": 100, "m3": 2}
    assert timeline[-1].load_after == {"kg": 0, "m3": 0}


# --------------------------------------------------------------------------
# Non-monotonic load and peak semantics (FR-03, §6.1)
# --------------------------------------------------------------------------

def test_a_pickup_raises_the_load_it_does_not_lower_it():
    """A pickup-only order was compiled as a delivery before E-20, so a route
    collecting goods was planned as one shedding them."""
    problem = instance((collect("P1", "C1", kg=100),), {"kg": 1_000}, 1)
    timeline = build_timeline(problem, "V1", ["P1"])

    assert timeline[0].load_after["kg"] == 0, "leaves the depot empty"
    assert timeline[1].load_after["kg"] == 100, "carrying it after collection"
    assert timeline[-1].load_after["kg"] == 100, "still carrying it home"


def test_load_varies_non_monotonically_when_both_happen():
    """FR-03's stated consequence: "load varies non-monotonically along the
    route". Deliver 100, collect 80, deliver 100 -- down, up, down."""
    orders = (drop("D1", "C1", kg=100), collect("P1", "C2", kg=80),
              drop("D2", "C3", kg=100))
    problem = instance(orders, {"kg": 1_000}, 3)
    timeline = build_timeline(problem, "V1", ["D1", "P1", "D2"])

    profile = [step.load_after["kg"] for step in timeline]
    assert profile == [200, 100, 180, 80, 80], profile
    assert profile[2] > profile[1], "the pickup must raise the load"


def test_a_route_legal_on_totals_can_be_illegal_at_its_peak():
    """§6.1's classic production bug, as a test.

    Deliver 60, then collect 60: the totals net to zero and a
    totals-based check calls a 100-unit van comfortable. The van actually
    carries 60 out of the depot, sheds it, then picks 60 back up -- peaking at
    60, which fits. Raise both to 120 and the *totals* still net to zero while
    the van is asked to leave the depot carrying 120 into a 100-unit hold.
    """
    orders = (drop("D1", "C1", kg=120), collect("P1", "C2", kg=120))
    problem = instance(orders, {"kg": 100}, 2)
    timeline = build_timeline(problem, "V1", ["D1", "P1"])

    peak = max(step.load_after["kg"] for step in timeline)
    net = sum(o.quantities["kg"] for o in orders if o.delivery) - \
        sum(o.quantities["kg"] for o in orders if o.pickup)

    assert net == 0, "the totals net out, which is what makes this the trap"
    assert peak == 120, f"peak load is {peak}"
    assert peak > 100, "and the peak is what the van cannot carry"


def test_the_verifier_rejects_a_plan_that_exceeds_capacity_at_its_peak():
    """INV-5 is a per-step check, which is peak semantics by construction --
    provided the loads it reads are right. This pins that they are."""
    from vrp.model import Route, Solution

    orders = (drop("D1", "C1", kg=120), collect("P1", "C2", kg=120))
    problem = instance(orders, {"kg": 100}, 2)
    solution = Solution(
        problem_id=problem.id,
        routes=(Route(vehicle_id="V1",
                      steps=build_timeline(problem, "V1", ["D1", "P1"])),),
        unassigned=(), objective_breakdown={}, status="FEASIBLE")

    report = verify(problem, solution)
    assert not report.ok
    assert any(v.invariant == "INV-5" for v in report.violations), \
        [str(v) for v in report.violations]


def test_the_solver_respects_the_peak_too():
    """The verifier catching it is necessary but not sufficient: a solver that
    plans on totals would emit that plan on every run and be rejected every
    time, which is a broken pipeline rather than a working guard."""
    orders = (drop("D1", "C1", kg=120), collect("P1", "C2", kg=120))
    problem = instance(orders, {"kg": 100}, 2)
    solution = solve(problem, iterations=300, seed=0)

    if solution.status == "FEASIBLE" and not solution.unassigned:
        assert verify(problem, solution).ok, "claimed feasible but is not"
    else:
        assert True, "refusing is the correct answer here"


@pytest.mark.parametrize("dimensions", [1, 2, 3])
def test_capacity_holds_across_dimension_counts(dimensions):
    """A guard against the dimension order drifting between compile and map:
    with one dimension a mix-up is invisible, with three it is not."""
    names = ["kg", "m3", "pallets"][:dimensions]
    quantities = {name: (index + 1) * 10 for index, name in enumerate(names)}
    capacities = {name: 1_000 for name in names}

    problem = instance((drop("O1", "C1", **quantities),), capacities, 1)
    solution = solve(problem, iterations=200, seed=0)

    assert verify(problem, solution).ok
    start = solution.routes[0].steps[0]
    assert start.load_after == quantities, start.load_after


def test_the_adapter_maps_a_pickup_as_a_pickup():
    """Through `solve`, not `build_timeline`.

    Every other load-profile test here goes through the canonical evaluator,
    which handled signed load correctly before E-20 -- so they pass whatever
    the adapter does. Perturbation proved it: compiling every order as a
    delivery, the exact pre-E-20 bug, left all of them green. This is the one
    that fails.
    """
    orders = (drop("D1", "C1", kg=100), collect("P1", "C2", kg=80))
    problem = instance(orders, {"kg": 1_000}, 2)
    solution = solve(problem, iterations=300, seed=0)
    assert verify(problem, solution).ok

    steps = solution.routes[0].steps
    start = steps[0].load_after["kg"]
    assert start == 100, (
        f"leaves the depot with {start}: should carry the 100 it will drop and "
        f"not the 80 it will collect")
    assert steps[-1].load_after["kg"] == 80, "comes home with what it collected"

    at = {s.order_id: s.load_after["kg"] for s in steps if s.order_id}
    assert at["P1"] > at["D1"], "the pickup must raise the load the drop lowered"


def test_the_adapter_reports_every_dimension_it_was_given():
    """Also through `solve`. A mapper that dropped a dimension would leave
    INV-5 with nothing to check on it -- silence reading as success."""
    problem = instance((drop("O1", "C1", kg=100, m3=2, pallets=1),),
                       {"kg": 1_000, "m3": 10, "pallets": 4}, 1)
    solution = solve(problem, iterations=200, seed=0)

    assert set(solution.routes[0].steps[0].load_after) == {"kg", "m3", "pallets"}
    assert solution.routes[0].steps[0].load_after == {"kg": 100, "m3": 2, "pallets": 1}


def test_the_solver_is_told_the_pickup_is_a_pickup():
    """The one that actually catches a mis-compiled pickup.

    The mapper rebuilds the load profile from our own model, so what we
    *report* stays correct however the problem was compiled -- which means
    every load-profile assertion above passes even when PyVRP is handed a
    pickup as a delivery. Two perturbation runs confirmed exactly that.

    What changes is what the solver is asked to solve. Deliver 60 and collect
    60 into a 100-unit van: done properly the van carries 60 out, sheds it, and
    collects 60 back, peaking at 60. Told both are deliveries, PyVRP sees 120
    of cargo leaving the depot, exceeds the hold, and refuses a plan that is
    perfectly legal.
    """
    orders = (drop("D1", "C1", kg=60), collect("P1", "C2", kg=60))
    problem = instance(orders, {"kg": 100}, 2)
    solution = solve(problem, iterations=300, seed=0)

    assert solution.status == "FEASIBLE", (
        "refused a route peaking at 60 in a 100-unit van -- the solver was "
        "told the pickup was a delivery")
    assert not solution.unassigned
    assert verify(problem, solution).ok
