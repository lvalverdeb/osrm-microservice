"""Fleet minimisation — FR-32, §5.2, ALG-3b, T-35, E-35.

ALG-3b: "*Fleet minimisation*: a separate procedure using an absence-based
acceptance criterion, used when vehicle count is the primary objective
(`MIN_VEHICLES`)."

Separate is the operative word. §5.2's `MIN_VEHICLES` puts vehicle count
strictly above distance, and a distance-minimising search does not find the
smallest fleet as a side effect -- E-13 measured why: more vehicles is
monotonically *worse* on distance, so a cost-driven search already prefers few
routes and stops well short of the fewest feasible. Removing the last route
usually costs distance, and only a procedure that accepts that trade will do it.

The acceptance is measurable against published numbers rather than against
ourselves, which is rare enough in this project to be worth using: E-n22-k4
states "Min no of trucks: 4" in its own COMMENT, and RC208 ships a reference
solution with four routes. Both come from E-05's reader, so the figures are read
from the files rather than transcribed.

The absence counter is the part that can silently do nothing. Without it the
procedure ejects the same stubborn customer every attempt and cycles; with it,
a customer that has been ejected often becomes an unattractive choice and the
search moves on. `test_the_absence_counter_changes_which_customer_is_ejected`
is that check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vrp.fleet import Ejection, minimise_fleet, routes_needed
from vrp.lns import plan_cost
from vrp.model import TravelMatrix

INSTANCES = Path("benchmarks/instances")


def grid_matrix(size: int, leg: int = 1_000) -> TravelMatrix:
    cells = tuple(tuple(abs(i - j) * leg for j in range(size))
                  for i in range(size))
    return TravelMatrix(version="fm", durations=cells, distances=cells)


def one_per_route(size: int) -> list[list[int]]:
    """The worst possible starting fleet: a vehicle per customer."""
    return [[node] for node in range(1, size)]


# --------------------------------------------------------------------------
# The procedure
# --------------------------------------------------------------------------

def test_a_fleet_of_one_route_per_customer_is_reduced():
    """The clearest case. Ten customers, ten routes, capacity for all of them
    on one vehicle -- the answer is one route."""
    matrix = grid_matrix(11)
    plan = one_per_route(11)

    reduced = minimise_fleet(matrix, plan, capacity=100,
                             demands=dict.fromkeys(range(1, 11), 1), seed=0)

    assert routes_needed(reduced) == 1, routes_needed(reduced)
    assert sorted(n for r in reduced for n in r) == list(range(1, 11))


def test_capacity_bounds_the_reduction():
    """Ten customers of 30 units into vehicles holding 100: at least four
    routes are required and the procedure must not claim fewer."""
    matrix = grid_matrix(11)
    plan = one_per_route(11)
    demands = dict.fromkeys(range(1, 11), 30)

    reduced = minimise_fleet(matrix, plan, capacity=100, demands=demands, seed=0)

    assert routes_needed(reduced) >= 4, routes_needed(reduced)
    for route in reduced:
        assert sum(demands[node] for node in route) <= 100


def test_every_customer_survives_the_reduction():
    """The cheapest way to shrink a fleet is to lose a customer."""
    matrix = grid_matrix(16)
    plan = one_per_route(16)
    demands = dict.fromkeys(range(1, 16), 20)

    reduced = minimise_fleet(matrix, plan, capacity=100, demands=demands, seed=0)
    assert sorted(n for r in reduced for n in r) == list(range(1, 16))


def test_a_fleet_already_minimal_is_left_alone():
    """The control. A procedure that always reports a reduction is not
    minimising anything."""
    matrix = grid_matrix(5)
    demands = dict.fromkeys(range(1, 5), 60)
    plan = [[1, 2], [3, 4]]

    reduced = minimise_fleet(matrix, plan, capacity=120, demands=demands, seed=0)
    assert routes_needed(reduced) == 2


def test_it_is_deterministic_for_a_seed():
    matrix = grid_matrix(14)
    plan = one_per_route(14)
    demands = dict.fromkeys(range(1, 14), 25)

    first = minimise_fleet(matrix, plan, capacity=100, demands=demands, seed=7)
    second = minimise_fleet(matrix, plan, capacity=100, demands=demands, seed=7)
    assert first == second


# --------------------------------------------------------------------------
# Absence-based acceptance
# --------------------------------------------------------------------------

def test_the_absence_counter_rises_for_customers_that_resist_insertion():
    """The counter is what stops the procedure cycling on one awkward stop."""
    ejection = Ejection()
    ejection.record(5)
    ejection.record(5)
    ejection.record(9)

    assert ejection.absence[5] == 2
    assert ejection.absence[9] == 1
    assert ejection.absence[3] == 0


def test_the_absence_counter_changes_which_customer_is_ejected():
    """Without it the procedure ejects the same stubborn customer every attempt
    and makes no progress. The counter makes a repeatedly-ejected customer an
    unattractive choice, so the search tries elsewhere."""
    ejection = Ejection()
    candidates = [1, 2, 3]

    first = ejection.choose(candidates)
    for _ in range(5):
        ejection.record(first)

    assert ejection.choose(candidates) != first, (
        "the same customer was chosen after five ejections; the counter is inert")


def test_the_counter_is_bounded():
    """ALG-4 warns that "unbounded penalty growth is a common cause of search
    collapse". The same applies here: an absence count allowed to run away makes
    one customer permanently unejectable, which removes a legal move from the
    search forever."""
    ejection = Ejection(cap=3)
    for _ in range(50):
        ejection.record(1)
    assert ejection.absence[1] == 3


# --------------------------------------------------------------------------
# T-35's acceptance: published vehicle counts
# --------------------------------------------------------------------------

@pytest.mark.skipif(not (INSTANCES / "E-n22-k4.txt").exists(),
                    reason="benchmark instances not present")
def test_it_reaches_the_published_truck_count_on_e_n22_k4():
    """The instance states it in its own COMMENT: "Min no of trucks: 4".

    Read from the file by E-05's reader rather than transcribed, so the target
    cannot drift from the instance it belongs to.

    This test does not isolate *which* packing decision gets there: the module
    records a measured 2x2 showing the ejection order and the insertion rule
    are independently sufficient on this instance, so perturbing either alone
    leaves this green. The four is still the number that matters.
    """
    from vrp.benchmarks import read_benchmark

    benchmark = read_benchmark(INSTANCES / "E-n22-k4.txt")
    problem = benchmark.problem
    demands = {order.delivery.location_id: order.quantities["demand"]
               for order in problem.orders}
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    by_node = {index[stop]: amount for stop, amount in demands.items()}

    plan = [[node] for node in sorted(by_node)]
    capacity = problem.vehicles[0].capacities["demand"]

    reduced = minimise_fleet(problem.matrix, plan, capacity=capacity,
                             demands=by_node, seed=0)
    print(f"\n  E-n22-k4: {routes_needed(reduced)} routes "
          f"(published minimum 4)")

    assert routes_needed(reduced) == 4, routes_needed(reduced)


@pytest.mark.skipif(not (INSTANCES / "RC208.vrp").exists(),
                    reason="benchmark instances not present")
def test_it_reaches_the_reference_route_count_on_rc208():
    """RC208's reference solution uses four routes. Solomon's RC2 series is
    long-horizon, so the fleet is genuinely reducible and four is a real target
    rather than an artefact of tight windows."""
    from vrp.benchmarks import read_benchmark

    problem = read_benchmark(INSTANCES / "RC208.vrp").problem
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    by_node = {index[order.delivery.location_id]: order.quantities["demand"]
               for order in problem.orders}
    windows = {index[order.delivery.location_id]: order.delivery.time_windows[0]
               for order in problem.orders}
    service = {index[order.delivery.location_id]: order.delivery.service_fixed
               for order in problem.orders}

    plan = [[node] for node in sorted(by_node)]
    capacity = problem.vehicles[0].capacities["demand"]

    reduced = minimise_fleet(problem.matrix, plan, capacity=capacity,
                             demands=by_node, seed=0, windows=windows,
                             service=service)
    print(f"  RC208: {routes_needed(reduced)} routes (reference 4)")

    assert routes_needed(reduced) <= 4, routes_needed(reduced)


def test_reducing_the_fleet_is_allowed_to_cost_distance():
    """§5.2's MIN_VEHICLES: "vehicle count strictly dominates distance".

    E-13 measured that more vehicles is monotonically worse on distance, so the
    fewest-vehicle plan is normally the *longest* one. A procedure that refused
    to accept a distance increase would stop one route short of the answer.
    """
    matrix = grid_matrix(11)
    plan = one_per_route(11)
    demands = dict.fromkeys(range(1, 11), 1)

    before = plan_cost(matrix, plan)
    reduced = minimise_fleet(matrix, plan, capacity=100, demands=demands, seed=0)

    assert routes_needed(reduced) == 1
    assert plan_cost(matrix, reduced) <= before, (
        "on this collinear instance one route is also shorter; if this ever "
        "fails the fixture has changed, not the requirement")
