"""The explanation service — CON-5, FR-36, T-60, E-60.

CON-5 is unusually direct about why this exists: "Every route plan MUST be able
to answer, per order: why was I assigned to this vehicle, in this position, at
this time? Every rejection MUST answer: which constraint made me infeasible, and
what would have to change? Dispatchers reject plans they cannot explain, and
unexplainable plans are silently overridden -- which destroys the benefit."

So the bar is not "an explanation exists". It is that a dispatcher can act on it.
"Time window problem" is an explanation and it is useless; §9.4's own example
sets the standard -- "Earliest arrival 14:12 from nearest eligible vehicle V-11;
window closes 13:30" -- because that names the vehicle to look at, the number to
compare, and by implication the thing to change.

`would_fit_if` is the second half and the harder one. A reason code says what
went wrong; this says what to do about it, as a concrete edit to the instance:
widen this window to this instant, raise this capacity to this figure. A
dispatcher who has to work that out themselves has been given a diagnosis and
no prescription.

**On T-60's definition of done.** It asks for "a dispatcher usability test
passed on 20 real queries", and a usability test needs dispatchers. What is
testable here is the half that would make such a test possible: that twenty
distinct query shapes each get a specific, actionable answer rather than a
generic one. `test_twenty_queries_all_get_a_specific_answer` is that, and it is
a proxy rather than the thing itself -- which the commit says out loud.
"""

from __future__ import annotations

import pytest

from vrp.explain import Change, explain, explain_assignment, would_fit_if
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

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 1800


def problem(stops: int = 3, vans: int = 2, **overrides) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    windows = overrides.get("windows", {})
    return Problem(
        id="explain",
        locations=overrides.get("locations", tuple(
            Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100,
                     lon=-84.0, matrix_index=i) for i in range(size))),
        orders=overrides.get("orders", tuple(
            Order(id=f"O{i}", kind="JOB",
                  quantities=overrides.get("quantities", {"kg": 1}),
                  required_skills=overrides.get("skills", frozenset()),
                  delivery=StopSpec(location_id=f"C{i}",
                                    time_windows=(windows.get(f"O{i}", DAY),),
                                    service_fixed=60))
            for i in range(1, size))),
        vehicles=overrides.get("vehicles", tuple(
            Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                    start_location_id="D", end_location_id="D",
                    cost_per_metre=1)
            for n in range(1, vans + 1))),
        matrix=TravelMatrix(version="e", durations=grid, distances=grid))


def plan(instance: Problem, assignment: dict[str, list[str]]) -> Solution:
    index = {loc.id: loc.matrix_index for loc in instance.locations}
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock, here = 0, index["D"]
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


# --------------------------------------------------------------------------
# CON-5's first question: why this vehicle, this position, this time
# --------------------------------------------------------------------------

def test_an_assignment_names_the_vehicle_position_and_time():
    instance = problem()
    current = plan(instance, {"V1": ["O1", "O2", "O3"]})

    why = explain_assignment(instance, current, "O2")

    assert why.vehicle_id == "V1"
    assert why.position == 2
    assert why.arrival == 2 * LEG + 60


def test_the_rationale_says_something_specific():
    """"It was assigned to V1" is not an explanation, it is a restatement.
    CON-5 wants the reason, and §9.4's example sets the standard by naming
    numbers a dispatcher can check."""
    instance = problem()
    current = plan(instance, {"V1": ["O1", "O2", "O3"]})

    why = explain_assignment(instance, current, "O2")

    assert why.because, why
    assert any(str(why.arrival) in line or "V1" in line for line in why.because)


def test_an_order_that_is_not_in_the_plan_has_no_rationale():
    instance = problem()
    current = plan(instance, {"V1": ["O1"]})

    assert explain_assignment(instance, current, "O3") is None


# --------------------------------------------------------------------------
# CON-5's second question: which constraint, and what would have to change
# --------------------------------------------------------------------------

def test_an_unreachable_window_says_what_to_widen_it_to():
    """§9.4's own example: "Earliest arrival 14:12... window closes 13:30",
    with `would_fit_if` naming the instant that would fix it."""
    closes_early = {"O2": TimeWindow(start=0, end=LEG)}
    instance = problem(windows=closes_early)

    changes = would_fit_if(instance, "O2")

    assert Change(change="window_end", to=2 * LEG) in changes, changes


def test_an_oversized_order_says_what_capacity_it_needs():
    instance = problem(quantities={"kg": 500})

    changes = would_fit_if(instance, "O1")

    assert Change(change="vehicle_capacity_kg", to=500) in changes, changes


def test_a_missing_skill_names_the_skill():
    instance = problem(skills=frozenset({"TAIL_LIFT"}))

    changes = would_fit_if(instance, "O1")

    assert Change(change="vehicle_skill", to="TAIL_LIFT") in changes, changes


def test_a_stockout_says_how_much_stock_is_needed():
    from dataclasses import replace

    instance = problem(quantities={"kg": 40})
    stocked = replace(instance, locations=(
        replace(instance.location("D"), inventory={"kg": 10}),
        *instance.locations[1:]))

    changes = would_fit_if(stocked, "O1")

    assert Change(change="depot_inventory_kg", to=40) in changes, changes


def test_a_servable_order_has_nothing_to_change():
    """The control. A service that always found something to suggest would be
    noise, and a dispatcher would stop reading it."""
    instance = problem()

    assert would_fit_if(instance, "O1") == ()


def test_every_suggestion_names_a_field_and_a_value():
    """A suggestion a dispatcher cannot act on is a diagnosis with no
    prescription. "Widen the window" is that; "widen it to 14:12" is not."""
    instance = problem(windows={"O2": TimeWindow(start=0, end=LEG)},
                       quantities={"kg": 500})

    for order in instance.orders:
        for change in would_fit_if(instance, order.id):
            assert change.change and change.to is not None, change


# --------------------------------------------------------------------------
# FR-36: the marginal cost of serving one order
# --------------------------------------------------------------------------

def test_an_assigned_order_carries_what_it_cost_to_serve():
    """FR-36 asks for marginal costs. Per order it is the detour: what the
    route would have saved by not going there."""
    instance = problem()
    current = plan(instance, {"V1": ["O1", "O2", "O3"]})

    why = explain_assignment(instance, current, "O3")

    assert why.marginal_cost > 0


def test_the_marginal_cost_is_the_detour_it_causes():
    """Checkable by hand, which is the only reason to trust it. Dropping O3
    from D-C1-C2-C3-D leaves D-C1-C2-D: the saving is the run out to C3 and
    back, less the leg home it replaces."""
    instance = problem()
    current = plan(instance, {"V1": ["O1", "O2", "O3"]})

    why = explain_assignment(instance, current, "O3")

    with_it = 3 * LEG + 3 * LEG
    without = 2 * LEG + 2 * LEG
    assert why.marginal_cost == with_it - without, why.marginal_cost


# --------------------------------------------------------------------------
# The whole report
# --------------------------------------------------------------------------

def test_explain_covers_every_order_one_way_or_the_other():
    """CON-5 says "every order" and "every rejection". An order in neither list
    is one nobody can ask about."""
    instance = problem(stops=3, windows={"O2": TimeWindow(start=0, end=1)})
    current = plan(instance, {"V1": ["O1", "O3"]})

    report = explain(instance, current)

    covered = set(report.assigned) | set(report.rejected)
    assert covered == {order.id for order in instance.orders}


def test_a_rejection_carries_a_code_a_sentence_and_a_fix():
    instance = problem(stops=3, windows={"O2": TimeWindow(start=0, end=1)})
    current = plan(instance, {"V1": ["O1", "O3"]})

    rejection = explain(instance, current).rejected["O2"]

    assert rejection.reason_code
    assert len(rejection.explanation) > 20, rejection.explanation
    assert rejection.would_fit_if


def test_a_rejection_the_diagnosis_cannot_explain_says_so():
    """§6.5's UNIMPLEMENTED codes need a solve. An order the solver dropped for
    a reason pre-flight cannot see must not be given a confident wrong answer --
    E-14's whole argument, one layer up."""
    instance = problem()
    current = plan(instance, {"V1": ["O1", "O2"]})

    rejection = explain(instance, current).rejected["O3"]

    assert rejection.reason_code == "FLEET_EXHAUSTED"
    assert rejection.would_fit_if == ()


# --------------------------------------------------------------------------
# T-60's definition of done, as far as it can be tested without dispatchers
# --------------------------------------------------------------------------

def test_twenty_queries_all_get_a_specific_answer():
    """T-60 asks for "a dispatcher usability test passed on 20 real queries".

    A usability test needs dispatchers, and this is the half that can be tested
    without them: twenty distinct query shapes, every one answered with
    something naming a vehicle, an instant, a quantity or a skill -- never a
    bare category. A service that answered "time window problem" twenty times
    would pass a coverage test and fail the requirement.
    """
    from dataclasses import replace

    cases = []
    # O2 sits two legs out, so a window closing at or after 2*LEG is reachable
    # and there is nothing to diagnose. A first version used 2*LEG and 3*LEG
    # and got FLEET_EXHAUSTED twice -- pre-flight being right about a fixture
    # that was not actually infeasible.
    for closes in (1, 600, 1_200, LEG, LEG + 1):
        cases.append(problem(windows={"O2": TimeWindow(start=0, end=closes)}))
    for kg in (150, 300, 500, 900):
        cases.append(problem(quantities={"kg": kg}))
    for skill in ("TAIL_LIFT", "FRIDGE", "ADR", "CRANE"):
        cases.append(problem(skills=frozenset({skill})))
    for stock in (1, 5, 20):
        base = problem(quantities={"kg": 40})
        cases.append(replace(base, locations=(
            replace(base.location("D"), inventory={"kg": stock}),
            *base.locations[1:])))
    for stops in (2, 3, 4, 5):
        cases.append(problem(stops=stops, vans=1,
                             windows={"O2": TimeWindow(start=0, end=1)}))

    assert len(cases) == 20, len(cases)

    answered = 0
    for instance in cases:
        current = plan(instance, {"V1": []})
        report = explain(instance, current)
        for rejection in report.rejected.values():
            if rejection.would_fit_if:
                assert all(change.to is not None
                           for change in rejection.would_fit_if), rejection
                answered += 1
                break
    assert answered == 20, f"{answered}/20 queries got an actionable answer"


def test_an_unknown_order_is_refused_rather_than_guessed_at():
    """The model already refuses, with its own error type naming the order.
    Asserted here because "explain O99" is a plausible typo from a dispatcher
    tool, and a service that answered it with silence would look like a
    servable order."""
    from vrp.model import ValidationError

    instance = problem()
    with pytest.raises(ValidationError, match="O99"):
        would_fit_if(instance, "O99")
