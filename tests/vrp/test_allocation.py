"""Operational allocation — FR-30, FR-33, FR-36, §7.8, T-44, E-44.

§7.8: "Allocation is solved *jointly* with routing by making vehicle deployment
endogenous: each vehicle carries a fixed cost that is charged only if it is
used, so the search decides deployment."

The word carrying the weight is *each*. `Vehicle.fixed_cost`, `cost_per_metre`
and `cost_per_second` have been on the model since E-21, and the model's own
comment says why they were moved there: costs living on `ObjectiveSpec` as one
set for everybody "made a 3.5-tonne van and an artic cost the same per
kilometre -- the 'H' in MDHVRPTW, decorative".

The canonical objective never caught up. It scores `Tier.FLEET` as a *count* of
deployed vehicles and converts it to money with a single flat rate, and it
scores operating cost with one flat rate per metre for the whole fleet. So the
model is heterogeneous, the PyVRP adapter prices each vehicle type separately --
and the evaluator that judges the result, the one INV-9 exists to make
authoritative, cannot tell a cheap fleet from an expensive one.

That is worse than a missing feature. A solver optimising per-vehicle costs and
an evaluator scoring flat ones disagree about which plan is better, and §11.2
says the evaluator wins. The cheaper plan loses.
"""

from __future__ import annotations

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
from vrp.objective import Mode, ObjectiveSpec, Tier, score

DAY = TimeWindow(start=0, end=12 * 3600)
LEG = 10_000


def instance(*vehicles: Vehicle, stops: int = 2, prize: int = 0,
             tier: int = 0) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    times = tuple(tuple(abs(i - j) * 600 for j in range(size))
                  for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1}, prize=prize,
              priority_tier=tier,
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    return Problem(id="alloc", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="a", durations=times,
                                       distances=grid))


def a_van(vehicle_id: str, **kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=vehicle_id, **{**defaults, **kwargs})


def plan(problem: Problem, assignment: dict[str, list[str]]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in problem.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index["D"]
        for order_id in order_ids:
            stop = problem.order(order_id).delivery
            there = index[stop.location_id]
            clock += problem.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += problem.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in problem.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


SPEC = ObjectiveSpec(mode=Mode.MIN_COST)


# --------------------------------------------------------------------------
# FR-30: each vehicle carries its own fixed cost
# --------------------------------------------------------------------------

def test_the_fleet_tier_charges_each_vehicle_its_own_fixed_cost():
    """§5.1: "Tier 3 Fleet cost: Sum of fixed_cost(v) over deployed vehicles".

    A count is not a sum unless every vehicle costs the same, which is exactly
    the homogeneity FR-07 exists to reject.
    """
    problem = instance(a_van("CHEAP", fixed_cost=10_000),
                       a_van("DEAR", fixed_cost=90_000))

    cheap = score(problem, plan(problem, {"CHEAP": ["O1", "O2"]}), SPEC)
    dear = score(problem, plan(problem, {"DEAR": ["O1", "O2"]}), SPEC)

    assert cheap.values[Tier.FLEET] == 10_000, cheap.values[Tier.FLEET]
    assert dear.values[Tier.FLEET] == 90_000, dear.values[Tier.FLEET]
    assert cheap.total < dear.total, (
        "the same route on a cheaper vehicle did not score better")


def test_a_fleet_that_prices_nothing_still_scores_the_way_it_used_to():
    """Every instance predating E-21 states its costs on the spec, and those
    instances must keep scoring exactly as they did -- the frozen corpus among
    them. So the spec's rates are the fallback for a fleet that prices nothing.
    """
    problem = instance(a_van("V1"))
    scored = score(problem, plan(problem, {"V1": ["O1", "O2"]}), SPEC)

    assert scored.values[Tier.FLEET] == SPEC.vehicle_fixed_cost


def test_a_free_vehicle_beside_a_priced_one_is_genuinely_free():
    """The fallback is fleet-wide, not per vehicle, and this is why.

    Own capacity is sunk cost and hired capacity is not -- FR-33's whole
    subject. If an unstated zero silently became the spec's default price, an
    own vehicle could not be expressed at all, and the own-vs-hire break-even
    would be a comparison between two hire prices.
    """
    # OWN states nothing at all. A per-vehicle fallback would read that as
    # "unpriced" and hand it the spec's default day rate, which is exactly the
    # confusion this rule exists to prevent -- so OWN must stay free while
    # HIRED, in the same fleet, keeps its price.
    problem = instance(a_van("OWN"),
                       a_van("HIRED", fixed_cost=70_000, cost_per_metre=1))

    own = score(problem, plan(problem, {"OWN": ["O1", "O2"]}), SPEC)
    hired = score(problem, plan(problem, {"HIRED": ["O1", "O2"]}), SPEC)

    assert own.values[Tier.FLEET] == 0
    assert hired.values[Tier.FLEET] == 70_000
    assert own.total < hired.total


def test_an_empty_route_is_free():
    """§7.8: "Empty routes are free and removable at zero cost." A vehicle
    listed in the plan but carrying nothing was never deployed."""
    problem = instance(a_van("V1", fixed_cost=50_000),
                       a_van("V2", fixed_cost=50_000))
    both = plan(problem, {"V1": ["O1", "O2"], "V2": []})
    one = plan(problem, {"V1": ["O1", "O2"]})

    assert (score(problem, both, SPEC).values[Tier.FLEET]
            == score(problem, one, SPEC).values[Tier.FLEET] == 50_000)


# --------------------------------------------------------------------------
# FR-07 / FR-30: operating cost is per vehicle too
# --------------------------------------------------------------------------

def test_operating_cost_uses_each_vehicle_s_own_rate():
    """The artic and the van again. Same route, same metres, different money."""
    problem = instance(a_van("VAN", cost_per_metre=1),
                       a_van("ARTIC", cost_per_metre=4))

    van = score(problem, plan(problem, {"VAN": ["O1", "O2"]}), SPEC)
    artic = score(problem, plan(problem, {"ARTIC": ["O1", "O2"]}), SPEC)

    assert artic.values[Tier.OPERATING] == 4 * van.values[Tier.OPERATING], (
        van.values[Tier.OPERATING], artic.values[Tier.OPERATING])


def test_time_cost_is_charged_per_vehicle_as_well():
    problem = instance(a_van("SLOWPAID", cost_per_second=1),
                       a_van("UNPAID", cost_per_second=0))

    paid = score(problem, plan(problem, {"SLOWPAID": ["O1"]}), SPEC)
    unpaid = score(problem, plan(problem, {"UNPAID": ["O1"]}), SPEC)

    assert paid.values[Tier.OPERATING] > unpaid.values[Tier.OPERATING]


def test_an_unpriced_fleet_falls_back_to_the_spec_s_distance_rate():
    problem = instance(a_van("V1"))
    spec = ObjectiveSpec(mode=Mode.MIN_COST, cost_per_metre=7)

    scored = score(problem, plan(problem, {"V1": ["O1"]}), spec)
    assert scored.values[Tier.OPERATING] == 2 * LEG * 7, (
        scored.values[Tier.OPERATING])


# --------------------------------------------------------------------------
# §5.2 MIN_COST: "deployed iff its fixed cost is repaid by savings"
# --------------------------------------------------------------------------

def test_hiring_a_vehicle_is_worth_it_only_when_the_work_repays_the_day():
    """The break-even T-44 asks to be reproduced on fixtures, at the only place
    one exists.

    Not between one vehicle and two. In a metric space merging two routes is
    never worse on distance -- the triangle inequality guarantees
    `d(D,A) + d(D,B) >= d(A,B)` -- so splitting cannot save distance and no
    fixed cost is ever repaid by splitting. A first draft of this test asserted
    exactly that and was wrong about arithmetic, not about the implementation.

    The real trade is FR-33's: hire a vehicle for a full day (OBJ-4's step
    cost), or forgo the work. In PRIZE_COLLECTING the two share a level and are
    compared in one currency, so the answer is arithmetic:

        serve  iff  fixed_cost + operating  <  prize forgone
    """
    spec = ObjectiveSpec(mode=Mode.PRIZE_COLLECTING)
    prize, round_trip = 100_000, 2 * LEG

    for hire, worth_it in ((prize - round_trip - 1, True),
                           (prize - round_trip + 1, False)):
        problem = instance(a_van("HIRED", fixed_cost=hire, cost_per_metre=1),
                           stops=1, prize=prize, tier=2)
        served = score(problem, plan(problem, {"HIRED": ["O1"]}), spec)
        declined = score(problem, plan(problem, {}), spec)

        if worth_it:
            assert served.total < declined.total, (
                f"a day costing {hire} against {prize} of work was declined")
        else:
            assert declined.total < served.total, (
                f"a day costing {hire} against {prize} of work was taken")


def test_min_vehicles_ignores_the_break_even_entirely():
    """§5.2: under MIN_VEHICLES, count strictly dominates cost.

    One expensive vehicle against two nearly free ones. In money the pair is
    cheaper by almost the whole fixed cost; under MIN_VEHICLES it must still
    lose, because no quantity of a lower tier buys any of a higher one.
    """
    problem = instance(a_van("DEAR", fixed_cost=90_000, cost_per_metre=1),
                       a_van("V2", fixed_cost=1, cost_per_metre=1),
                       a_van("V3", fixed_cost=1, cost_per_metre=1))
    one = plan(problem, {"DEAR": ["O1", "O2"]})
    two = plan(problem, {"V2": ["O1"], "V3": ["O2"]})

    cheap = ObjectiveSpec(mode=Mode.MIN_COST)
    assert score(problem, two, cheap).total < score(problem, one, cheap).total

    strict = ObjectiveSpec(mode=Mode.MIN_VEHICLES)
    assert score(problem, one, strict).total < score(problem, two, strict).total
