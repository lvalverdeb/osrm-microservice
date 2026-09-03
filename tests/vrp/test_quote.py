"""Insertion and removal quotes — NFR-02, §9.4, T-85.

`NFR-02`: "Interactive latency. Single-order insertion / removal quote: p95 ≤ 2
s. Locked re-optimisation of one region: p95 ≤ 30 s." §9.4 gives the second
clause its endpoint: `POST /v1/solutions/{id}/quote -> insertion price for
candidate order(s), T0 latency`.

`T-56` already meets the 30-second clause and `tests/vrp/test_reoptimisation_latency.py`
measures it. This is the other one, and the point of a quote is that it does
*not* re-plan: a dispatcher on the phone to a customer wants a price for adding
this stop to today's round, not tomorrow's round rearranged.

**"Fast" is the easy half.** A quote that returned zero instantly would pass a
latency test. So the price is checked against the canonical evaluator run over
both plans, the chosen position is checked for legality, and a quote is
required to leave every other route exactly where it was — which is what
separates a quote from a re-solve.
"""

from __future__ import annotations

import time

import pytest

from vrp.evaluator import ObjectiveWeights, evaluate
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.quote import NoRoomForOrder, quote_insertion, quote_removal

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def instance(stops: int = 12, vans: int = 4, capacity: int = 100,
             leg: int = LEG) -> Problem:
    """Stops strung along a road, `leg` seconds apart.

    `leg` shrinks for the large instance: at 600 s a four-hundredth stop is
    sixty-six hours from the depot and no route containing it is legal, so the
    latency test would measure how fast a refusal is rather than how fast a
    quote is.
    """
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * leg for j in range(size))
                 for i in range(size))
    return Problem(
        id="quote",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": capacity},
                               shift=DAY, start_location_id="D",
                               end_location_id="D", cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=TravelMatrix(version="q", durations=grid, distances=grid))


def spread(problem: Problem, vans: int, reserve: int = 1
           ) -> dict[str, list[str]]:
    """Every order but the last `reserve` dealt round-robin across the fleet."""
    served = [order.id for order in problem.orders][:-reserve]
    assignment: dict[str, list[str]] = {f"V{n}": [] for n in range(1, vans + 1)}
    for position, order_id in enumerate(served):
        assignment[f"V{position % vans + 1}"].append(order_id)
    return assignment


def cost_of(problem: Problem, assignment: dict[str, list[str]]) -> int:
    """The canonical objective less the penalty for what is not served.

    The penalty dominates — one unserved order is worth a million against
    forty-five thousand of distance here — so including it would make every
    insertion a saving. What a quote prices is the cost of serving.
    """
    result = evaluate(problem, assignment, ObjectiveWeights())
    return result.total - result.breakdown.get("unassigned_penalty", 0)


# --------------------------------------------------------------------------
# The price is the price
# --------------------------------------------------------------------------

def test_the_quoted_price_is_what_the_plan_actually_costs_more():
    """Checked against the canonical evaluator over both plans, not against
    the scan's own arithmetic. An engine scoring its own work is what CON-1
    exists to prevent, and a quote is an engine."""
    problem = instance()
    before = spread(problem, vans=4)
    candidate = problem.orders[-1].id

    quote = quote_insertion(problem, before, candidate)

    after = {vehicle: list(orders) for vehicle, orders in before.items()}
    after[quote.vehicle_id] = list(quote.route)
    assert quote.price == cost_of(problem, after) - cost_of(problem, before)
    assert quote.price > 0, "adding a stop somewhere cost nothing"


def test_the_quote_picks_a_position_that_is_actually_legal():
    problem = instance()
    before = spread(problem, vans=4)
    quote = quote_insertion(problem, before, problem.orders[-1].id)

    from vrp.evaluator import route_is_legal
    assert route_is_legal(problem, quote.vehicle_id, list(quote.route))
    assert problem.orders[-1].id in quote.route


def test_the_quote_is_the_cheapest_available_placement():
    """Otherwise it is a price, not a quote."""
    problem = instance()
    before = spread(problem, vans=4)
    candidate = problem.orders[-1].id
    quote = quote_insertion(problem, before, candidate)

    from vrp.evaluator import route_is_legal
    for vehicle_id, orders in before.items():
        for position in range(len(orders) + 1):
            route = orders[:position] + [candidate] + orders[position:]
            if not route_is_legal(problem, vehicle_id, route):
                continue
            trial = {v: list(o) for v, o in before.items()}
            trial[vehicle_id] = route
            assert cost_of(problem, trial) - cost_of(problem, before) >= \
                quote.price, (
                f"{vehicle_id}@{position} is cheaper than the quote")


# --------------------------------------------------------------------------
# A quote is not a re-plan
# --------------------------------------------------------------------------

def test_a_quote_moves_nothing_that_was_already_planned():
    """The property that separates a quote from a re-solve.

    A dispatcher pricing a stop for a customer on the phone is not asking for
    today's round to be rearranged, and a "quote" that returned a better plan
    with everything moved would be unusable however cheap it said the stop was.
    """
    problem = instance()
    before = spread(problem, vans=4)
    snapshot = {vehicle: list(orders) for vehicle, orders in before.items()}

    quote = quote_insertion(problem, before, problem.orders[-1].id)

    assert before == snapshot, "quoting mutated the plan it was asked about"
    for vehicle_id, orders in snapshot.items():
        if vehicle_id == quote.vehicle_id:
            # The chosen route keeps its own orders in their original order.
            assert [o for o in quote.route if o != problem.orders[-1].id] == orders
        else:
            assert quote.vehicle_id != vehicle_id


# --------------------------------------------------------------------------
# Removal
# --------------------------------------------------------------------------

def test_removing_an_order_quotes_a_saving():
    problem = instance()
    before = spread(problem, vans=4, reserve=1)
    # The farthest stop on the route. These locations are strung out along a
    # line, so removing a *middle* one costs the same journey — the van drives
    # past it either way — and the test would be measuring the geometry rather
    # than the quote.
    victim = before["V1"][-1]

    quote = quote_removal(problem, before, victim)

    after = {v: [o for o in orders if o != victim]
             for v, orders in before.items()}
    assert quote.price == cost_of(problem, after) - cost_of(problem, before)
    assert quote.price < 0, "dropping a stop saved nothing"
    assert victim not in quote.route


def test_an_insertion_and_its_removal_price_the_same_journey():
    """Insert, then quote removing what was just inserted: the two prices are
    equal and opposite. A quote that failed this would be pricing something
    other than the stop."""
    problem = instance()
    before = spread(problem, vans=4)
    candidate = problem.orders[-1].id

    insertion = quote_insertion(problem, before, candidate)
    after = {v: list(o) for v, o in before.items()}
    after[insertion.vehicle_id] = list(insertion.route)

    removal = quote_removal(problem, after, candidate)
    assert removal.price == -insertion.price


def test_removing_an_order_nobody_is_carrying_is_refused():
    problem = instance()
    with pytest.raises(KeyError, match="O12"):
        quote_removal(problem, {"V1": ["O1"]}, "O12")


# --------------------------------------------------------------------------
# When there is no answer
# --------------------------------------------------------------------------

def test_an_order_no_vehicle_can_take_is_refused_by_name():
    """CON-11: what cannot be priced is refused rather than quoted at
    infinity. A dispatcher told "£99,999,999" reads it as expensive; one told
    "no vehicle has room" hires a van."""
    problem = instance(stops=6, vans=1, capacity=3)
    before = {"V1": ["O1", "O2", "O3"]}

    with pytest.raises(NoRoomForOrder, match="O6"):
        quote_insertion(problem, before, "O6")


def test_an_empty_fleet_is_refused_rather_than_priced():
    problem = instance()
    with pytest.raises(NoRoomForOrder):
        quote_insertion(problem, {}, problem.orders[-1].id)


# --------------------------------------------------------------------------
# NFR-02's first clause
# --------------------------------------------------------------------------

def test_a_quote_returns_within_the_interactive_budget():
    """NFR-02: p95 ≤ 2 s. Twenty runs, the way the 30-second clause is
    measured, because a latency claim from one measurement is a best case
    quoted as a guarantee."""
    problem = instance(stops=400, vans=40, leg=4)
    before = spread(problem, vans=40)

    timings = []
    for run in range(20):
        candidate = problem.orders[-1].id
        started = time.monotonic()
        quote = quote_insertion(problem, before, candidate)
        timings.append(time.monotonic() - started)
        assert quote.price > 0

    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 <= 2.0, f"p95 {p95:.2f}s over {len(timings)} runs"
    print(f"\n  p95 {p95 * 1000:.0f} ms, worst {max(timings) * 1000:.0f} ms, "
          f"{len(problem.orders)} stops across {len(problem.vehicles)} vans")
