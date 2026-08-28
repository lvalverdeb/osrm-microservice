"""Trigger engine, locked re-optimisation and the delta — DYN-5, AC-2.1, AC-2.3,
§8.3, §8.4, T-56, E-56.

US-2: "when a vehicle breaks down at 11:00, I re-optimise only the affected and
nearby work while everything already executed or committed stays fixed."

Three requirements meet here and each is easy to satisfy on its own while
missing the point of the other two:

* **DYN-5** wants re-optimisation to be *event driven* -- "on breakdown,
  cancellation, large ETA drift, new priority order" -- rather than on a timer.
* **AC-2.1** bounds it: "A re-optimisation with 90% of stops locked returns in
  ≤ 30 seconds." §8.4 puts it in the T1 tier and says how: "Locked LNS on
  affected + neighbouring routes only." The budget is met by *not re-solving
  the plan*, not by solving it faster.
* **AC-2.3** says what comes back: "The response reports the delta versus the
  previous plan (stops moved, cost change, new lateness) rather than only the
  new plan."

§8.3 adds the reason the delta matters: "A 0.5% cost gain that reshuffles half
the plan at 14:00 is a net loss." A response that returns only a plan makes that
trade invisible, and a dispatcher accepts it without ever being asked.

Most of this composes rather than builds. T-50 already turns executed work into
locks and already computes which stops changed vehicle, which is AC-2.3's first
field. What is new is deciding *what a disruption touches*, keeping everything
else out of the search, and pricing what the answer cost in churn as well as
money.
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
from vrp.triggers import (
    Trigger,
    affected_routes,
    reoptimise,
)

HOUR = 3600
DAY = TimeWindow(start=0, end=12 * HOUR)
LEG = 600


def problem(stops: int = 12, vans: int = 4) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="react",
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
        matrix=TravelMatrix(version="t", durations=grid, distances=grid))


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


ROUTES = {"V1": ["O1", "O2", "O3"], "V2": ["O4", "O5", "O6"],
          "V3": ["O7", "O8", "O9"], "V4": ["O10", "O11", "O12"]}


# --------------------------------------------------------------------------
# DYN-5: the four triggers
# --------------------------------------------------------------------------

def test_every_trigger_dyn_5_names_is_supported():
    """DYN-5 lists four events. A trigger engine that handled three would be
    silent exactly when the fourth happened."""
    for kind in ("BREAKDOWN", "CANCELLATION", "ETA_DRIFT", "PRIORITY_ORDER"):
        assert Trigger(kind=kind, at=HOUR, vehicle_id="V1", order_id="O1")


def test_an_unknown_trigger_kind_is_refused():
    with pytest.raises(ValueError, match="trigger"):
        Trigger(kind="VOLCANO", at=HOUR)


def test_a_breakdown_needs_the_vehicle_that_broke():
    """A trigger missing its subject constrains nothing while looking like an
    instruction -- §6.6's objection to a lock without a subject, one layer up."""
    with pytest.raises(ValueError, match="vehicle_id"):
        Trigger(kind="BREAKDOWN", at=HOUR)


def test_a_cancellation_needs_the_order_that_was_cancelled():
    with pytest.raises(ValueError, match="order_id"):
        Trigger(kind="CANCELLATION", at=HOUR)


# --------------------------------------------------------------------------
# §8.4's T1 tier: only the affected and neighbouring routes
# --------------------------------------------------------------------------

def test_a_breakdown_affects_the_broken_vehicle():
    instance = problem()
    current = plan(instance, ROUTES)
    touched = affected_routes(instance, current,
                              Trigger("BREAKDOWN", HOUR, vehicle_id="V2"),
                              neighbours=0)

    assert touched == {"V2"}


def test_neighbouring_routes_are_pulled_in():
    """§8.4: "Locked LNS on affected + neighbouring routes only". The broken
    van's work has to go somewhere, and the only candidates worth searching are
    the routes near it."""
    instance = problem()
    current = plan(instance, ROUTES)
    touched = affected_routes(instance, current,
                              Trigger("BREAKDOWN", HOUR, vehicle_id="V2"),
                              neighbours=1)

    assert "V2" in touched
    assert len(touched) == 2


def test_most_of_the_plan_is_left_alone():
    """The whole method. AC-2.1's budget is met by not re-solving the plan, not
    by solving it faster -- a re-optimisation that touched everything would be
    a fresh solve wearing a different name."""
    instance = problem(stops=40, vans=10)
    routes = {f"V{n}": [f"O{i}" for i in range(4 * n - 3, 4 * n + 1)]
              for n in range(1, 11)}
    current = plan(instance, routes)

    touched = affected_routes(instance, current,
                              Trigger("BREAKDOWN", HOUR, vehicle_id="V5"),
                              neighbours=1)

    assert len(touched) <= 2, touched


def test_a_cancellation_affects_the_route_that_carried_it():
    instance = problem()
    current = plan(instance, ROUTES)
    touched = affected_routes(instance, current,
                              Trigger("CANCELLATION", HOUR, order_id="O8"),
                              neighbours=0)

    assert touched == {"V3"}


# --------------------------------------------------------------------------
# AC-2.2 still holds: executed work does not move
# --------------------------------------------------------------------------

def test_committed_work_survives_a_breakdown_elsewhere():
    """§8.3: re-optimisation "MUST respect `FREEZE_UNTIL` and never move
    executed work". T-50 built the locks; this is the first thing that could
    have ignored them."""
    instance = problem()
    current = plan(instance, ROUTES)

    response = reoptimise(instance, current,
                          Trigger("BREAKDOWN", 2 * HOUR, vehicle_id="V4"),
                          now=2 * HOUR)

    before = {step.order_id for route in current.routes
              for step in route.steps
              if step.order_id and step.start_service <= 2 * HOUR}
    after = {step.order_id for route in response.plan.routes
             for step in route.steps
             if step.order_id and step.start_service <= 2 * HOUR}

    assert before <= after, before - after


def test_the_broken_vehicles_work_is_not_lost():
    """A breakdown moves work; it does not delete it. Silently dropping the
    orders would make the delta look excellent."""
    instance = problem()
    current = plan(instance, ROUTES)

    response = reoptimise(instance, current,
                          Trigger("BREAKDOWN", 0, vehicle_id="V2"), now=0)

    placed = {step.order_id for route in response.plan.routes
              for step in route.steps if step.order_id}
    dropped = {entry["order_id"] for entry in response.plan.unassigned}

    assert placed | dropped == {order.id for order in instance.orders}


def test_a_broken_vehicle_is_not_used_again():
    """Built so the broken van is the only place the work could go.

    Two earlier fixtures proved nothing. On the ordinary instance any route can
    absorb the orders and cheapest-insertion picked another one anyway; putting
    V2's stops in their own corner did not help either, because on this
    geometry a dedicated trip out and back always costs more than a detour on a
    van already going that way. Removing the guard changed no result in both.

    So V1 is capacity-bound. It cannot take the displaced work at any price,
    and a re-planner allowed to use the broken van would put it straight back
    on the broken van -- which is what the guard exists to prevent.
    """
    from dataclasses import replace as _replace

    instance = problem(stops=6, vans=2)
    instance = _replace(instance, vehicles=(
        _replace(instance.vehicles[0], capacities={"kg": 3}),
        instance.vehicles[1]))
    current = plan(instance, {"V1": ["O1", "O2", "O3"],
                              "V2": ["O4", "O5", "O6"]})

    response = reoptimise(instance, current,
                          Trigger("BREAKDOWN", 0, vehicle_id="V2"), now=0,
                          neighbours=1)

    broken = next(route for route in response.plan.routes
                  if route.vehicle_id == "V2")
    assert not [step for step in broken.steps if step.order_id], broken

    # The work is not lost either -- it is reported, which is what a dispatcher
    # needs when a van goes down and nothing else can carry its load.
    stranded = {entry["order_id"] for entry in response.plan.unassigned}
    assert stranded == {"O4", "O5", "O6"}, response.plan.unassigned


# --------------------------------------------------------------------------
# AC-2.3: the delta, not just the plan
# --------------------------------------------------------------------------

def test_the_response_reports_the_three_fields_ac_2_3_names():
    """"stops moved, cost change, new lateness". Three numbers, because they
    move independently: a cheaper plan that reshuffles half the fleet and a
    cheaper plan that touches nothing are different answers."""
    instance = problem()
    current = plan(instance, ROUTES)

    delta = reoptimise(instance, current,
                       Trigger("BREAKDOWN", 0, vehicle_id="V2"), now=0).delta

    assert isinstance(delta.moved, dict)
    assert delta.cost_after - delta.cost_before == delta.cost_change
    assert delta.lateness_after >= 0


def test_stops_moved_are_named_rather_than_counted():
    """A count tells a dispatcher how bad it is. The names tell them who to
    ring."""
    instance = problem()
    current = plan(instance, ROUTES)

    delta = reoptimise(instance, current,
                       Trigger("BREAKDOWN", 0, vehicle_id="V2"), now=0).delta

    assert set(delta.moved) >= {"O4", "O5", "O6"}, delta.moved
    assert all(was == "V2" for was, _ in delta.moved.values())


def test_an_uneventful_reoptimisation_reports_no_churn():
    """The control. A delta that always found something to report would be
    noise, and §8.3's churn argument would be unusable."""
    instance = problem()
    current = plan(instance, ROUTES)

    delta = reoptimise(instance, current,
                       Trigger("ETA_DRIFT", 0, vehicle_id="V1"),
                       now=0, neighbours=0).delta

    assert delta.moved == {}
    assert delta.cost_change == 0


# --------------------------------------------------------------------------
# AC-2.1: the budget
# --------------------------------------------------------------------------

def test_a_re_optimisation_with_ninety_percent_locked_is_fast():
    """AC-2.1: "A re-optimisation with 90% of stops locked returns in ≤ 30
    seconds", and T-56's definition of done asks for that at p95.

    Twenty runs rather than one, because a latency claim from a single
    measurement is a best case quoted as a guarantee.
    """
    import time

    instance = problem(stops=100, vans=20)
    routes = {f"V{n}": [f"O{i}" for i in range(5 * n - 4, 5 * n + 1)]
              for n in range(1, 21)}
    current = plan(instance, routes)

    timings = []
    for run in range(20):
        broken = f"V{run % 20 + 1}"
        started = time.monotonic()
        response = reoptimise(instance, current,
                              Trigger("BREAKDOWN", 0, vehicle_id=broken),
                              now=0)
        timings.append(time.monotonic() - started)
        assert response.plan is not None

    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 <= 30.0, f"p95 {p95:.2f}s over {len(timings)} runs"
    print(f"\n  p95 {p95 * 1000:.0f} ms, worst {max(timings) * 1000:.0f} ms, "
          f"{len(routes)} routes, one re-optimised")


def test_the_locked_share_is_reported():
    """AC-2.1 is a claim about a *locked* re-optimisation, so the response has
    to say how much was locked. Otherwise "fast" could just mean the search
    quietly skipped everything."""
    instance = problem(stops=40, vans=10)
    routes = {f"V{n}": [f"O{i}" for i in range(4 * n - 3, 4 * n + 1)]
              for n in range(1, 11)}
    current = plan(instance, routes)

    response = reoptimise(instance, current,
                          Trigger("BREAKDOWN", 0, vehicle_id="V5"), now=0)

    assert response.locked_share >= 800, response.locked_share
