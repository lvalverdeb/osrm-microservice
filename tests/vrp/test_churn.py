"""Stability and the churn/cost trade-off — §8.3, T-57, E-57.

§8.3: "Re-optimisation MUST be **stability-aware**: report and optionally
penalise churn (stops moved between vehicles, ETA shifts communicated to
customers). A 0.5% cost gain that reshuffles half the plan at 14:00 is a net
loss. OR-Tools exposes solution-similarity-to-previous machinery for exactly
this; where an engine does not, implement churn as a Tier-6 objective term."

Three separate instructions in one paragraph, and T-56 only carried out the
first. It *reports* churn; §8.3 also asks to optionally *penalise* it, and says
where -- Tier 6, beside T-47's imbalance. And it asks for the choice to be an
operational one rather than a hard-coded weight, which is what T-57's definition
of done means by "churn/cost trade-off curve produced for operations to choose a
point".

**Two kinds of churn, and they are not the same disruption.** A stop moving to
another van is a driver's problem: a route they had not planned for, an address
they do not know. A stop keeping its van but shifting an hour is a customer's
problem: somebody was told a time and it is now wrong. §8.3 names both, so both
are counted, and a fleet can weigh them differently because for most operations
they are not equally expensive.

**Why the curve rather than a constant.** The right penalty depends on what an
operation's churn actually costs -- a courier network re-planning every ten
minutes and a grocery delivery with booked slots are not the same business. A
hard-coded weight would be this codebase making that decision on their behalf,
which is exactly what "for operations to choose a point" rules out.
"""

from __future__ import annotations

import pytest

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
from vrp.stability import Churn, churn, churn_cost, tradeoff
from vrp.triggers import Trigger, reoptimise

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def problem(stops: int = 9, vans: int = 3) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="churn",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=60))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                               start_location_id="D", end_location_id="D",
                               cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=TravelMatrix(version="c", durations=grid, distances=grid))


def plan(instance: Problem, assignment: dict[str, list[str]],
         starts: dict[str, int] | None = None) -> Solution:
    index = {loc.id: loc.matrix_index for loc in instance.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        clock = (starts or {}).get(vehicle_id, 0)
        steps = [Step(type="START", location_id="D", arrival=clock,
                      start_service=clock, departure=clock)]
        here = index["D"]
        for order_id in order_ids:
            stop = instance.order(order_id).delivery
            there = index[stop.location_id]
            clock += instance.matrix.duration(here, there)
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock, here = clock + 60, there
        clock += instance.matrix.duration(here, index["D"])
        steps.append(Step(type="END", location_id="D", arrival=clock,
                          start_service=clock, departure=clock))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    return Solution(
        problem_id=instance.id, routes=tuple(routes),
        unassigned=tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                          "explanation": "-"}
                         for o in instance.orders if o.id not in served),
        objective_breakdown={}, status="FEASIBLE")


ROUTES = {"V1": ["O1", "O2", "O3"], "V2": ["O4", "O5", "O6"],
          "V3": ["O7", "O8", "O9"]}


# --------------------------------------------------------------------------
# §8.3's two kinds of churn
# --------------------------------------------------------------------------

def test_an_unchanged_plan_has_no_churn():
    instance = problem()
    current = plan(instance, ROUTES)

    assert churn(current, current) == Churn(moved=0, eta_shift=0)


def test_a_stop_changing_vehicle_counts_as_moved():
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6", "O3"],
                            "V3": ["O7", "O8", "O9"]})

    assert churn(before, after).moved == 1


def test_a_stop_keeping_its_van_but_shifting_counts_as_an_eta_change():
    """§8.3 names both, and they are not the same disruption. A stop moving to
    another van is a driver's problem -- an unplanned route, an address they do
    not know. A stop keeping its van and shifting an hour is a customer's
    problem: somebody was told a time and it is now wrong."""
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, ROUTES, starts={"V1": HOUR})

    changed = churn(before, after)
    assert changed.moved == 0
    assert changed.eta_shift == 3 * HOUR, changed


def test_the_two_are_counted_separately():
    """A fleet can weigh them differently, because for most operations they are
    not equally expensive. Summing them at source would take that choice away.
    """
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6", "O3"],
                            "V3": ["O7", "O8", "O9"]}, starts={"V3": HOUR})

    changed = churn(before, after)
    assert changed.moved > 0 and changed.eta_shift > 0


def test_a_moved_stop_is_not_also_charged_for_its_new_time():
    """It is already counted once, and it is one disruption.

    O3 changes vehicle *and* is served at a different time -- moving it could
    hardly do otherwise. Counting the shift as well would make reassignment
    look worse than it is for a reason nobody reading the number could trace,
    and would push any weighted comparison towards leaving work stranded.
    """
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6", "O3"],
                            "V3": ["O7", "O8", "O9"]})

    was = {step.order_id: step.start_service for route in before.routes
           for step in route.steps if step.order_id}
    now = {step.order_id: step.start_service for route in after.routes
           for step in route.steps if step.order_id}
    assert was["O3"] != now["O3"], "fixture: O3 must actually move in time"

    changed = churn(before, after)
    assert changed.moved == 1
    assert changed.eta_shift == 0, changed


def test_a_dropped_stop_counts_as_moved():
    """It is the largest disruption there is: somebody is not being served."""
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6"],
                            "V3": ["O7", "O8", "O9"]})

    assert churn(before, after).moved == 1


# --------------------------------------------------------------------------
# The price of it
# --------------------------------------------------------------------------

def test_churn_cost_prices_both_kinds():
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6", "O3"],
                            "V3": ["O7", "O8", "O9"]}, starts={"V3": HOUR})

    changed = churn(before, after)
    priced = churn_cost(before, after, per_move=1_000, per_second=2)

    assert priced == changed.moved * 1_000 + changed.eta_shift * 2


def test_a_zero_weight_prices_nothing():
    """§8.3 says "optionally penalise". A fleet that does not care must be able
    to say so, and get the behaviour it had before T-57."""
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": [], "V2": ["O4", "O5", "O6"],
                            "V3": ["O7", "O8", "O9", "O1", "O2", "O3"]})

    assert churn_cost(before, after, per_move=0, per_second=0) == 0


# --------------------------------------------------------------------------
# §8.3: "implement churn as a Tier-6 objective term"
# --------------------------------------------------------------------------

def test_tier_six_carries_churn_when_a_previous_plan_is_given():
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6", "O3"],
                            "V3": ["O7", "O8", "O9"]})
    spec = ObjectiveSpec(mode=Mode.MIN_COST)

    without = score(instance, after, spec)
    with_previous = score(instance, after, spec, previous=before)

    assert with_previous.values[Tier.QUALITY] > without.values[Tier.QUALITY]


def test_scoring_without_a_previous_plan_is_unchanged():
    """Every caller predating T-57 must keep scoring exactly as it did. There
    is no churn without something to churn against, and inventing a baseline
    would rewrite the objective for every static solve in the system."""
    instance = problem()
    current = plan(instance, ROUTES)
    spec = ObjectiveSpec(mode=Mode.MIN_COST)

    assert score(instance, current, spec).values[Tier.QUALITY] == \
        score(instance, current, spec, previous=None).values[Tier.QUALITY]


def test_churn_never_outranks_a_real_cost():
    """Tier 6 is the bottom of §5.1, and §8.3 puts churn there deliberately.
    Checked on the scales rather than on a pair of plans, for the reason T-47
    found: a fixture only shows the property at the magnitudes it happens to
    produce."""
    from vrp.objective import tier_scales

    instance = problem()
    spec = ObjectiveSpec(mode=Mode.MIN_COST)
    scales = tier_scales(instance, spec)
    bounds = spec.tier_bounds(instance)

    assert bounds[Tier.QUALITY] * scales[Tier.QUALITY] < scales[Tier.OPERATING]


# --------------------------------------------------------------------------
# T-57's definition of done: the curve
# --------------------------------------------------------------------------

def test_a_heavier_penalty_moves_fewer_stops():
    """The mechanism the curve is made of. Without it the weight is decorative
    and every point on the curve is the same plan.

    Driven by an ETA drift rather than a breakdown, and the difference matters.
    When a van breaks its work has nowhere to stay -- every candidate route is
    a move, so the penalty is a constant added to every option and changes
    nothing. Churn is unavoidable there, and a curve drawn on a breakdown is
    flat for a correct reason that looks exactly like a broken weight.
    """
    instance = problem(stops=9, vans=3)
    current = plan(instance, ROUTES)
    trigger = Trigger("ETA_DRIFT", 0, vehicle_id="V1")

    loose = reoptimise(instance, current, trigger, now=0, neighbours=2,
                       churn_weight=0)
    tight = reoptimise(instance, current, trigger, now=0, neighbours=2,
                       churn_weight=10 ** 6)

    assert tight.delta.churn < loose.delta.churn, (tight.delta.churn,
                                                   loose.delta.churn)


def test_a_breakdown_cannot_be_made_stable_at_any_price():
    """The other side of the same fact, stated so nobody reads the flat curve
    above as a bug. A van that has broken cannot keep its work, so no penalty
    buys stability -- and a weight that appeared to would be lying."""
    instance = problem(stops=9, vans=3)
    current = plan(instance, ROUTES)
    trigger = Trigger("BREAKDOWN", 0, vehicle_id="V2")

    free = reoptimise(instance, current, trigger, now=0, churn_weight=0)
    dear = reoptimise(instance, current, trigger, now=0, churn_weight=10 ** 7)

    assert free.delta.churn == dear.delta.churn > 0


def test_the_curve_has_more_than_one_point():
    """T-57: "Churn/cost trade-off curve produced for operations to choose a
    point". A flat curve offers no choice, and would mean the penalty is not
    controlling anything."""
    instance = problem(stops=9, vans=3)
    current = plan(instance, ROUTES)
    trigger = Trigger("ETA_DRIFT", 0, vehicle_id="V1")

    curve = tradeoff(instance, current, trigger, now=0, neighbours=2,
                     weights=(0, 1_000, 100_000, 10 ** 7))

    assert len(curve) == 4
    assert len({(point.churn, point.cost) for point in curve}) > 1, curve


def test_the_curve_is_ordered_by_weight():
    """So it reads as a curve rather than a set of unrelated runs."""
    instance = problem(stops=9, vans=3)
    current = plan(instance, ROUTES)
    trigger = Trigger("ETA_DRIFT", 0, vehicle_id="V1")

    curve = tradeoff(instance, current, trigger, now=0, neighbours=2,
                     weights=(100_000, 0, 1_000))

    assert [point.weight for point in curve] == [0, 1_000, 100_000]


def test_cheaper_plans_churn_more():
    """§8.3's whole argument, made measurable: "A 0.5% cost gain that reshuffles
    half the plan at 14:00 is a net loss." The curve is what lets an operation
    decide whether their 0.5% is worth their reshuffle."""
    instance = problem(stops=9, vans=3)
    current = plan(instance, ROUTES)
    trigger = Trigger("ETA_DRIFT", 0, vehicle_id="V1")

    curve = tradeoff(instance, current, trigger, now=0, neighbours=2,
                     weights=(0, 10 ** 7))

    cheapest = min(curve, key=lambda point: point.cost)
    steadiest = min(curve, key=lambda point: point.churn)
    assert cheapest.churn > steadiest.churn, curve
    assert cheapest.cost < steadiest.cost, curve


def test_a_curve_needs_weights():
    instance = problem()
    with pytest.raises(ValueError, match="weight"):
        tradeoff(instance, plan(instance, ROUTES),
                 Trigger("BREAKDOWN", 0, vehicle_id="V2"), now=0, weights=())
