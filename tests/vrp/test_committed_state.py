"""The committed-state manager — DYN-4, AC-2.2, §8.3, T-50, E-50.

US-2: "As a dispatcher, when a vehicle breaks down at 11:00, I re-optimise only
the affected and nearby work while everything already executed or committed
stays fixed." AC-2.2 states the hard part in one line: "No stop already visited
or currently en route is moved."

DYN-4 says how: a component that "converts executed and en-route work into
`FIX_ROUTE_PREFIX` / `FREEZE_UNTIL` locks (§6.6)". Both lock kinds have existed
since T-29 and INV-8 has enforced them since. What was missing is the thing that
produces them -- so re-optimisation at 11:00 was free to reorder the morning.

Two details carry the requirement, and both are easy to get almost right:

**En route is committed.** A van three minutes from a stop has not visited it,
and moving it means a driver turning around in the street. AC-2.2 names both
states in one breath for that reason, and a manager that pinned only completed
work would pass every test written against completed work.

**The freeze horizon is not the same thing as the prefix.** `FIX_ROUTE_PREFIX`
pins *what* each vehicle has done; `FREEZE_UNTIL` stops the optimiser filling
the morning with new work around it. Emitting one without the other leaves a
gap: a plan can honour every prefix and still schedule a fresh stop at 09:00.

T-50's definition of done is "no executed stop ever moves, proven by replay
tests", so `test_a_replayed_day_never_moves_executed_work` is the acceptance
rather than any single-epoch assertion.
"""

from __future__ import annotations

import pytest

from vrp.committed import (
    Execution,
    commit_locks,
    committed_prefix,
    moved_since,
)
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


def problem(stops: int = 6, vans: int = 2) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 100, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    vehicles = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for n in range(1, vans + 1))
    return Problem(id="commit", locations=locations, orders=orders,
                   vehicles=vehicles,
                   matrix=TravelMatrix(version="k", durations=grid,
                                       distances=grid))


def plan(instance: Problem, assignment: dict[str, list[str]],
         locks: tuple = ()) -> Solution:
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


ROUTES = {"V1": ["O1", "O2", "O3"], "V2": ["O4", "O5", "O6"]}


# --------------------------------------------------------------------------
# AC-2.2: what counts as committed
# --------------------------------------------------------------------------

def test_nothing_is_committed_before_the_day_starts():
    instance = problem()
    current = plan(instance, ROUTES)

    assert committed_prefix(instance, current.routes[0], now=0) == []


def test_a_served_stop_is_committed():
    instance = problem()
    current = plan(instance, ROUTES)
    # O1 is served at 600 and the van leaves at 660.
    assert committed_prefix(instance, current.routes[0], now=700) == ["O1"]


def test_the_stop_being_driven_to_is_committed_though_not_yet_visited():
    """AC-2.2 names both states in one breath: "already visited *or currently
    en route*". A van three minutes from a stop has not visited it, and moving
    it means a driver turning around in the street.

    A manager that pinned only completed work would pass every test written
    against completed work, which is why this one exists.
    """
    instance = problem()
    current = plan(instance, ROUTES)
    # At 700 the van has left O1 (660) and reaches O2 at 1,260.
    assert committed_prefix(instance, current.routes[0], now=700) == ["O1"]
    assert committed_prefix(instance, current.routes[0], now=700,
                            include_en_route=True) == ["O1", "O2"]


def test_a_stop_being_served_right_now_is_committed():
    instance = problem()
    current = plan(instance, ROUTES)
    # O1 is under service between 600 and 660.
    assert committed_prefix(instance, current.routes[0], now=630) == ["O1"]


def test_the_whole_route_is_committed_once_the_day_is_done():
    instance = problem()
    current = plan(instance, ROUTES)

    assert committed_prefix(instance, current.routes[0],
                            now=12 * 3600) == ["O1", "O2", "O3"]


def test_an_explicit_execution_record_overrides_the_clock():
    """Real telematics beats an inferred timeline. A van that is running late
    has executed less than the plan says it has, and believing the plan would
    pin work that has not happened.
    """
    instance = problem()
    current = plan(instance, ROUTES)
    behind = Execution(completed={"V1": ("O1",)}, en_route={"V1": "O2"})

    assert committed_prefix(instance, current.routes[0], now=12 * 3600,
                            include_en_route=True,
                            execution=behind) == ["O1", "O2"]


# --------------------------------------------------------------------------
# DYN-4: the locks it produces
# --------------------------------------------------------------------------

def test_it_emits_a_prefix_lock_per_working_vehicle():
    instance = problem()
    locks = commit_locks(instance, plan(instance, ROUTES), now=700)

    prefixes = {lock.vehicle_id: list(lock.order_ids) for lock in locks
                if lock.kind == "FIX_ROUTE_PREFIX"}
    # V1 has served O1 (departed 660) and is driving to O2. V2's first stop is
    # four legs out, so at 700 it is still en route to O4 and has served
    # nothing -- committed all the same.
    assert prefixes == {"V1": ["O1", "O2"], "V2": ["O4"]}


def test_it_emits_one_freeze_at_the_current_instant():
    """The prefix says *what* each vehicle has done; the freeze stops the
    optimiser filling the morning around it. Emitting one without the other
    leaves a gap -- a plan can honour every prefix and still schedule a fresh
    stop at 09:00."""
    instance = problem()
    locks = commit_locks(instance, plan(instance, ROUTES), now=700)

    freezes = [lock for lock in locks if lock.kind == "FREEZE_UNTIL"]
    assert len(freezes) == 1
    assert freezes[0].instant == 700


def test_a_vehicle_with_nothing_committed_gets_no_prefix_lock():
    """An empty prefix constrains nothing while looking like an instruction --
    §6.6's own objection to a lock without a subject."""
    instance = problem()
    locks = commit_locks(instance, plan(instance, ROUTES), now=0)

    assert not [lock for lock in locks if lock.kind == "FIX_ROUTE_PREFIX"]


def test_the_locks_it_produces_are_accepted_by_the_verifier():
    """The locks have to mean something to INV-8, or the manager is emitting
    decoration. T-29 built the checker; T-50 has to speak to it."""
    instance = problem()
    current = plan(instance, ROUTES)
    locks = commit_locks(instance, current, now=700)

    from dataclasses import replace
    locked = replace(instance, locks=locks)
    report = verify(locked, plan(locked, ROUTES))

    assert not [v for v in report.violations if v.invariant == "INV-8"], report


def test_a_replan_that_reorders_the_morning_is_caught_by_inv_8():
    """The whole point, end to end. Without T-50's locks this plan is
    unremarkable; with them it is the optimiser rewriting the past."""
    from dataclasses import replace

    instance = problem()
    locks = commit_locks(instance, plan(instance, ROUTES), now=700)
    locked = replace(instance, locks=locks)

    reordered = plan(locked, {"V1": ["O2", "O1", "O3"], "V2": ["O4", "O5", "O6"]})
    report = verify(locked, reordered)

    assert [v for v in report.violations if v.invariant == "INV-8"]


# --------------------------------------------------------------------------
# AC-2.3: what moved
# --------------------------------------------------------------------------

def test_an_unchanged_plan_moved_nothing():
    instance = problem()
    current = plan(instance, ROUTES)

    assert moved_since(current, current) == {}


def test_a_stop_changing_vehicle_is_reported():
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6", "O3"]})

    assert moved_since(before, after) == {"O3": ("V1", "V2")}


def test_reordering_within_one_vehicle_is_not_a_move():
    """AC-2.3 counts "stops moved between vehicles". A resequence is a
    different fact and belongs in the ETA delta, not this one."""
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O3", "O2", "O1"], "V2": ["O4", "O5", "O6"]})

    assert moved_since(before, after) == {}


def test_a_dropped_stop_is_reported_as_moving_nowhere():
    instance = problem()
    before = plan(instance, ROUTES)
    after = plan(instance, {"V1": ["O1", "O2"], "V2": ["O4", "O5", "O6"]})

    assert moved_since(before, after) == {"O3": ("V1", None)}


# --------------------------------------------------------------------------
# T-50's definition of done
# --------------------------------------------------------------------------

def test_a_replayed_day_never_moves_executed_work():
    """T-50: "No executed stop ever moves, proven by replay tests".

    A day replayed epoch by epoch. At each epoch the committed prefix is
    recomputed and a fresh plan is produced that is free to do anything with
    the open work -- here, deliberately reversing it. What was committed at any
    earlier epoch must still be there, in the same order, on the same vehicle,
    at every later one.

    Asserted across the whole horizon rather than at one instant, because a
    manager can be right at 11:00 and wrong at 13:00: the prefix only grows,
    and a bug that recomputes it from a changed plan loses the earlier half.
    """
    instance = problem(stops=6, vans=2)
    current = plan(instance, ROUTES)
    history: dict[str, list[str]] = {"V1": [], "V2": []}

    for now in range(0, 12 * 3600, 600):
        for route in current.routes:
            prefix = committed_prefix(instance, route, now,
                                      include_en_route=True)
            earlier = history[route.vehicle_id]
            assert prefix[:len(earlier)] == earlier, (
                f"at {now}s, {route.vehicle_id} lost committed work: "
                f"{earlier} -> {prefix}")
            history[route.vehicle_id] = prefix

        # Re-plan the open work as destructively as the rules allow.
        locks = commit_locks(instance, current, now)
        open_work = {
            route.vehicle_id: [step.order_id for step in route.steps
                               if step.order_id]
            for route in current.routes}
        for vehicle_id, ids in open_work.items():
            fixed = history[vehicle_id]
            open_work[vehicle_id] = fixed + list(reversed(ids[len(fixed):]))
        current = plan(instance, open_work, locks)

    # Not "the original order survived" -- at t=0 nothing is committed yet and
    # reversing the route is entirely legal, so the day may well execute in an
    # order the opening plan did not have. That is re-optimisation working. The
    # property is the one asserted inside the loop: whatever became committed
    # stayed committed, in place, at every later epoch.
    executed = sorted(order for prefix in history.values() for order in prefix)
    assert executed == [f"O{i}" for i in (1, 2, 3, 4, 5, 6)], history
    assert all(len(set(prefix)) == len(prefix) for prefix in history.values())


def test_the_committed_prefix_only_ever_grows():
    """The property the replay above rests on, stated directly."""
    instance = problem()
    current = plan(instance, ROUTES)

    seen: list[str] = []
    for now in range(0, 12 * 3600, 300):
        prefix = committed_prefix(instance, current.routes[0], now,
                                  include_en_route=True)
        assert prefix[:len(seen)] == seen, (now, seen, prefix)
        seen = prefix


def test_a_freeze_in_the_past_is_refused():
    instance = problem()
    with pytest.raises(ValueError, match="negative"):
        commit_locks(instance, plan(instance, ROUTES), now=-1)
