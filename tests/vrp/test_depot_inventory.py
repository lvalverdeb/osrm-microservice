"""Depot inventory and depot choice — FR-31, DEC-1, T-45, E-45.

FR-31: "Where multiple depots can serve an order, choose the depot as part of
optimisation, subject to inventory availability per depot."

Two halves, and only one of them is new. Multi-depot fleets have worked since
E-21: vehicles carry their own start location, so a plan already chooses which
depot serves an order by choosing which vehicle does. What was missing is the
clause after the comma. A depot is not a spring. It holds a finite amount of
each thing, and a plan that loads 30 tonnes out of a depot holding 24 is not an
expensive plan or a late one -- it is a plan that cannot happen, and every
invariant in the system passed it.

§7.8 puts the constraint at Layer E and calls it *global*: "a global inventory
constraint enforced at Layer E". DEC-1 says the same thing from the other end --
"depot inventory, dock capacity and shared-vehicle constraints MUST be enforced
globally, never per cluster" -- which is the case that makes this worth having
at all. Two sub-problems each drawing 20 tonnes from a 24-tonne depot are both
individually fine, and the plan they concatenate to is fiction. That is exactly
the failure §7.6's decomposition invites, and `test_two_routes_may_not_between_them_exceed_the_depot`
is the one that catches it.

`DEPOT_STOCKOUT` has been declared UNIMPLEMENTED since E-14, with the honest
reason "depot inventory is not modelled". This gives it a subject.
"""

from __future__ import annotations

import pytest

from vrp.diagnose import UNIMPLEMENTED, preflight
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

DAY = TimeWindow(start=0, end=12 * 3600)
LEG = 600


def instance(depots: tuple[Location, ...], vehicles: tuple[Vehicle, ...],
             stops: int = 2, kg: int = 10) -> Problem:
    customers = tuple(
        Location(id=f"C{i}", lat=9.9 + i / 100, lon=-84.0,
                 matrix_index=len(depots) + i - 1)
        for i in range(1, stops + 1))
    locations = (*depots, *customers)
    size = len(locations)
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": kg},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, stops + 1))
    return Problem(id="inv", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="i", durations=grid,
                                       distances=grid))


def depot(depot_id: str, index: int, **kwargs) -> Location:
    return Location(id=depot_id, lat=9.9, lon=-84.0, matrix_index=index,
                    **kwargs)


def a_van(vehicle_id: str, home: str, **kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY}
    return Vehicle(id=vehicle_id, start_location_id=home, end_location_id=home,
                   **{**defaults, **kwargs})


def plan(problem: Problem, assignment: dict[str, list[str]]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        home = problem.vehicle(vehicle_id).start_location_id
        steps = [Step(type="START", location_id=home, arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index[home]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            clock += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += problem.matrix.duration(here, index[home])
        steps.append(Step(type="END", location_id=home, arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in problem.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


def stockouts(report) -> list[str]:
    return [v.detail for v in report.violations if v.invariant == "INV-13"]


# --------------------------------------------------------------------------
# INV-13: a depot holds a finite amount
# --------------------------------------------------------------------------

def test_drawing_more_than_the_depot_holds_is_rejected():
    problem = instance((depot("D", 0, inventory={"kg": 15}),),
                       (a_van("V1", "D"),), stops=2, kg=10)

    assert stockouts(verify(problem, plan(problem, {"V1": ["O1", "O2"]})))


def test_drawing_what_the_depot_holds_is_accepted():
    """The control, and the boundary: exactly emptying a depot is legal."""
    problem = instance((depot("D", 0, inventory={"kg": 20}),),
                       (a_van("V1", "D"),), stops=2, kg=10)

    assert verify(problem, plan(problem, {"V1": ["O1", "O2"]})).ok


def test_a_depot_declaring_no_inventory_is_unconstrained():
    """Unset means unlimited, the same reading `dock_capacity` already has.
    Treating an unmeasured depot as empty would make every existing plan
    fiction the moment one depot started declaring stock."""
    problem = instance((depot("D", 0),), (a_van("V1", "D"),),
                       stops=2, kg=10_000)

    assert verify(problem, plan(problem, {"V1": ["O1", "O2"]})).ok


def test_inventory_is_tracked_per_dimension():
    """A depot full of pallets and out of chilled space is out of chilled
    space. One number cannot say that, and the binding dimension is not always
    the one somebody thought to check."""
    problem = instance((depot("D", 0, inventory={"kg": 100, "pallets": 1}),),
                       (a_van("V1", "D", capacities={"kg": 500,
                                                     "pallets": 10}),),
                       stops=2, kg=10)
    problem = _with_pallets(problem)

    failures = stockouts(verify(problem, plan(problem, {"V1": ["O1", "O2"]})))
    assert failures and "pallets" in failures[0], failures


def test_a_dimension_the_depot_does_not_stock_is_unconstrained():
    """A depot that declares kilograms says nothing about pallets. Reading the
    silence as zero would reject every order carrying a dimension the depot
    never thought to count."""
    problem = _with_pallets(
        instance((depot("D", 0, inventory={"kg": 100}),),
                 (a_van("V1", "D", capacities={"kg": 500, "pallets": 10}),),
                 stops=2, kg=10))

    assert verify(problem, plan(problem, {"V1": ["O1", "O2"]})).ok


# --------------------------------------------------------------------------
# DEC-1: global, not per route and not per cluster
# --------------------------------------------------------------------------

def test_two_routes_may_not_between_them_exceed_the_depot():
    """DEC-1: "depot inventory... MUST be enforced globally, never per cluster".

    This is the failure the invariant exists for. Each van draws 10 of a
    15-unit depot, so each route is individually fine and no per-route check
    can see anything wrong. The plan still loads 20 out of a depot holding 15.
    """
    problem = instance((depot("D", 0, inventory={"kg": 15}),),
                       (a_van("V1", "D"), a_van("V2", "D")), stops=2, kg=10)

    per_route = plan(problem, {"V1": ["O1"], "V2": ["O2"]})
    assert stockouts(verify(problem, per_route))


def test_each_depot_is_counted_separately():
    """Two depots holding 15 each can serve 20 between them -- but only if the
    work is split. Summing inventory across depots would accept a plan drawing
    all 20 from one of them."""
    depots = (depot("D1", 0, inventory={"kg": 15}),
              depot("D2", 1, inventory={"kg": 15}))
    problem = instance(depots, (a_van("V1", "D1"), a_van("V2", "D2")),
                       stops=2, kg=10)

    split = plan(problem, {"V1": ["O1"], "V2": ["O2"]})
    assert verify(problem, split).ok

    lopsided = plan(problem, {"V1": ["O1", "O2"]})
    assert stockouts(verify(problem, lopsided))


def test_the_invariant_is_not_applicable_when_no_depot_declares_stock():
    """§4.3's rule, and this project has been bitten by ignoring it twice: an
    invariant nothing can reach passes by never being asked, which is
    indistinguishable from one that holds."""
    problem = instance((depot("D", 0),), (a_van("V1", "D"),))
    report = verify(problem, plan(problem, {"V1": ["O1", "O2"]}))

    assert "INV-13" in report.not_applicable


def test_the_invariant_is_evaluated_as_soon_as_one_depot_declares_stock():
    problem = instance((depot("D", 0, inventory={"kg": 100}),),
                       (a_van("V1", "D"),))
    report = verify(problem, plan(problem, {"V1": ["O1", "O2"]}))

    assert "INV-13" not in report.not_applicable


# --------------------------------------------------------------------------
# §6.5: the reason code, before any solve
# --------------------------------------------------------------------------

def test_preflight_reports_a_stockout():
    """§6.5: "no depot with inventory can serve it in window"."""
    problem = instance((depot("D", 0, inventory={"kg": 5}),),
                       (a_van("V1", "D"),), stops=1, kg=10)

    found = preflight(problem)
    assert found["O1"].code == "DEPOT_STOCKOUT", found["O1"]


def test_an_order_one_depot_can_supply_is_not_a_stockout():
    """Pre-flight asks whether *some* depot could supply it, one order at a
    time. A depot that is short does not make the order unservable while
    another holds enough."""
    depots = (depot("D1", 0, inventory={"kg": 5}),
              depot("D2", 1, inventory={"kg": 50}))
    problem = instance(depots, (a_van("V1", "D1"), a_van("V2", "D2")),
                       stops=1, kg=10)

    assert "O1" not in preflight(problem)


def test_depot_stockout_is_no_longer_declared_unimplemented():
    """E-14 declared it UNIMPLEMENTED with the reason "depot inventory is not
    modelled". It is modelled now, and a code that stays on that list after it
    starts being emitted tells callers to keep waiting for something that has
    already arrived."""
    assert "DEPOT_STOCKOUT" not in UNIMPLEMENTED


# --------------------------------------------------------------------------
# Model validation
# --------------------------------------------------------------------------

def test_negative_inventory_is_refused():
    with pytest.raises(ValueError, match="inventory"):
        depot("D", 0, inventory={"kg": -1})


def _with_pallets(problem: Problem) -> Problem:
    """Re-state every order as also occupying one pallet."""
    from dataclasses import replace

    orders = tuple(replace(order, quantities={**order.quantities, "pallets": 1})
                   for order in problem.orders)
    return replace(problem, orders=orders)


# --------------------------------------------------------------------------
# Enforced in the plan, not only reported about it — T-72
# --------------------------------------------------------------------------

def _two_depots(stock_at_d: int, stock_at_d2: int, orders: int = 6) -> Problem:
    """Work in the middle, two depots either side, one of them short."""
    locations = ((Location(id="D", lat=9.9, lon=-84.00, matrix_index=0,
                           inventory={"kg": stock_at_d}),
                  Location(id="D2", lat=9.9, lon=-84.20, matrix_index=1,
                           inventory={"kg": stock_at_d2}))
                 + tuple(Location(id=f"C{i}", lat=9.9, lon=-84.10,
                                  matrix_index=2 + i)
                         for i in range(orders)))
    size = len(locations)
    grid = tuple(tuple(0 if i == j else 600 for j in range(size))
                 for i in range(size))
    return Problem(
        id="inv", locations=locations,
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 10},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=60))
                     for i in range(orders)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 60}, shift=DAY,
                               start_location_id=depot, end_location_id=depot)
                       for n, depot in enumerate(("D", "D2"), start=1)),
        matrix=TravelMatrix(version="inv", durations=grid, distances=grid))


def test_work_moves_off_a_depot_that_cannot_supply_it():
    """DEC-1: "depot inventory... MUST be enforced globally, never per
    cluster". The whole round fits, but not out of one yard."""
    from vrp.depots import drawn_per_depot, over_drawn, solve_within_inventory
    from vrp.solve.pyvrp_adapter import solve

    problem = _two_depots(stock_at_d=20, stock_at_d2=100)

    solution, planned = solve_within_inventory(
        problem, lambda p: solve(p, iterations=400, seed=0))

    drawn = drawn_per_depot(planned, solution)
    assert not over_drawn(planned, solution), drawn
    assert drawn.get("D", {}).get("kg", 0) <= 20
    assert verify(planned, solution).ok


def test_a_round_that_no_depot_can_supply_is_not_silently_shrunk():
    """Where the stock genuinely is not there, the loop runs out rather than
    quietly returning a smaller day. Pre-flight's `DEPOT_STOCKOUT` is the
    honest report, and it runs before any of this."""
    import pytest

    from vrp.depots import solve_within_inventory
    from vrp.solve.pyvrp_adapter import solve

    problem = _two_depots(stock_at_d=10, stock_at_d2=10)

    with pytest.raises(RuntimeError, match="supply more than they hold"):
        solve_within_inventory(problem,
                               lambda p: solve(p, iterations=200, seed=0),
                               max_rounds=3)


def test_the_withdrawal_protects_the_work_that_was_declared_to_matter():
    """§5.1's order of business, reused: the lowest tier goes first."""
    from dataclasses import replace

    from vrp.depots import solve_within_inventory
    from vrp.solve.pyvrp_adapter import solve

    problem = _two_depots(stock_at_d=20, stock_at_d2=100)
    tiered = replace(problem, orders=tuple(
        replace(order, priority_tier=0 if index < 2 else 2)
        for index, order in enumerate(problem.orders)))

    _solution, planned = solve_within_inventory(
        tiered, lambda p: solve(p, iterations=400, seed=0))

    withdrawn = {lock.order_id for lock in planned.locks}
    protected = {o.id for o in tiered.orders if o.priority_tier == 0}
    assert not (withdrawn & protected) or withdrawn >= {
        o.id for o in tiered.orders if o.priority_tier == 2}, (
        f"tier-0 work {sorted(withdrawn & protected)} was withdrawn while "
        "tier-2 work stayed on the depot that could not supply it")
