"""Operator locks and INV-8 — §6.6, FR-21, CON-7, part of T-29.

§6.6: "Locks are hard constraints. If locks make the instance infeasible, the
system MUST return `INFEASIBLE` with the minimal conflicting lock set (an
IIS-style diagnosis), never silently drop a lock."

INV-8 — "all locks are satisfied exactly" — has been reported *not applicable*
since E-03, for the honest reason that nothing could express a lock. This gives
it a subject. That matters beyond tidiness: an invariant nothing can reach
passes by never being asked, which is indistinguishable from an invariant that
holds, and this project has already been bitten twice by exactly that shape
(INV-5's missing loads, INV-2's unreachable shipments).

Every kind gets a plan that satisfies it and a plan that breaks it. A checker
written only against satisfying plans cannot fail, which is the same trap one
level up.

**Scope.** This is the verifier half of `T-29`. Making the *solver* honour a
lock, and diagnosing a conflicting set down to a minimal irreducible core, are
the other halves and are not here — so a lock today is a rule the verifier
enforces on a plan, not yet a rule the solver plans within.
"""

from __future__ import annotations

import pytest

from vrp.model import (
    Location,
    Lock,
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


def problem(locks: tuple[Lock, ...] = (), stops: int = 3,
            vehicles: int = 2) -> Problem:
    size = stops + 1
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 1000, lon=-84.0,
                 matrix_index=i)
        for i in range(size))
    grid = tuple(tuple(abs(i - j) * 600 for j in range(size)) for i in range(size))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", time_windows=(DAY,),
                                service_fixed=60))
        for i in range(1, size))
    fleet = tuple(
        Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                start_location_id="D", end_location_id="D")
        for n in range(1, vehicles + 1))
    return Problem(id="locks", locations=locations, orders=orders,
                   vehicles=fleet,
                   matrix=TravelMatrix(version="l", durations=grid, distances=grid),
                   locks=locks)


def plan(assignment: dict[str, list[str]], problem_: Problem) -> Solution:
    """A timeline-shaped plan. Times are nominal: INV-8 is about placement."""
    routes = []
    for vehicle_id, order_ids in assignment.items():
        steps = [Step(type="START", location_id="D", arrival=0,
                      start_service=0, departure=0)]
        clock = 0
        for order_id in order_ids:
            clock += 600
            stop = problem_.order(order_id).delivery
            steps.append(Step(type="DELIVERY", location_id=stop.location_id,
                              order_id=order_id, arrival=clock,
                              start_service=clock, departure=clock + 60))
            clock += 60
        steps.append(Step(type="END", location_id="D", arrival=clock + 600,
                          start_service=clock + 600, departure=clock + 600))
        routes.append(Route(vehicle_id=vehicle_id, steps=tuple(steps)))
    served = {o for ids in assignment.values() for o in ids}
    unassigned = tuple({"order_id": o.id, "reason_code": "NOT_PLACED",
                        "explanation": "not in the assignment"}
                       for o in problem_.orders if o.id not in served)
    return Solution(problem_id=problem_.id, routes=tuple(routes),
                    unassigned=unassigned, objective_breakdown={},
                    status="FEASIBLE")


def violations_of(report) -> list[str]:
    return [v.detail for v in report.violations if v.invariant == "INV-8"]


def test_inv_8_is_not_applicable_when_nothing_is_locked():
    """Silence is only honest when nobody asked for anything."""
    report = verify(problem(), plan({"V1": ["O1", "O2", "O3"]}, problem()))
    assert "INV-8" in report.not_applicable


def test_inv_8_is_evaluated_as_soon_as_a_lock_exists():
    """The point of the exercise: the invariant stops being a no-op."""
    p = problem((Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1"),))
    report = verify(p, plan({"V1": ["O1", "O2", "O3"]}, p))
    assert "INV-8" not in report.not_applicable
    assert not violations_of(report)


# --------------------------------------------------------------------------
# One satisfied and one broken plan per kind (§6.6)
# --------------------------------------------------------------------------

def test_pin_order_to_vehicle():
    lock = Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1", vehicle_id="V1")
    p = problem((lock,))

    assert not violations_of(verify(p, plan({"V1": ["O1"], "V2": ["O2"]}, p)))
    assert violations_of(verify(p, plan({"V1": ["O2"], "V2": ["O1"]}, p)))


def test_forbid_order_on_vehicle():
    lock = Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="O1", vehicle_id="V1")
    p = problem((lock,))

    assert not violations_of(verify(p, plan({"V2": ["O1"]}, p)))
    assert violations_of(verify(p, plan({"V1": ["O1"]}, p)))


def test_fix_route_prefix():
    """Work already executed or en route. The prefix must be exactly that --
    a prefix -- not merely present somewhere in the route."""
    lock = Lock(kind="FIX_ROUTE_PREFIX", vehicle_id="V1",
                order_ids=("O1", "O2"))
    p = problem((lock,))

    assert not violations_of(verify(p, plan({"V1": ["O1", "O2", "O3"]}, p)))
    assert violations_of(verify(p, plan({"V1": ["O3", "O1", "O2"]}, p))), \
        "the prefix was pushed down the route"
    assert violations_of(verify(p, plan({"V1": ["O2", "O1"]}, p))), \
        "the prefix order was swapped"


def test_fix_sequence():
    """Relative order preserved, but other stops may come between."""
    lock = Lock(kind="FIX_SEQUENCE", vehicle_id="V1", order_ids=("O1", "O3"))
    p = problem((lock,))

    assert not violations_of(verify(p, plan({"V1": ["O1", "O2", "O3"]}, p)))
    assert not violations_of(verify(p, plan({"V1": ["O1", "O3"]}, p)))
    assert violations_of(verify(p, plan({"V1": ["O3", "O1"]}, p)))


def test_force_deploy():
    lock = Lock(kind="FORCE_DEPLOY", vehicle_id="V2")
    p = problem((lock,))

    assert not violations_of(verify(p, plan({"V1": ["O1"], "V2": ["O2"]}, p)))
    assert violations_of(verify(p, plan({"V1": ["O1", "O2"]}, p))), \
        "V2 was told to go out and did not"


def test_forbid_deploy():
    lock = Lock(kind="FORBID_DEPLOY", vehicle_id="V2")
    p = problem((lock,))

    assert not violations_of(verify(p, plan({"V1": ["O1", "O2"]}, p)))
    assert violations_of(verify(p, plan({"V1": ["O1"], "V2": ["O2"]}, p)))


def test_pin_depot():
    """The order must be served by a vehicle starting from that depot."""
    lock = Lock(kind="PIN_DEPOT", order_id="O1", depot_id="D")
    p = problem((lock,))

    assert not violations_of(verify(p, plan({"V1": ["O1"]}, p)))

    elsewhere = Lock(kind="PIN_DEPOT", order_id="O1", depot_id="OTHER")
    q = problem((elsewhere,))
    assert violations_of(verify(q, plan({"V1": ["O1"]}, q))), \
        "served from D while pinned to OTHER"


def test_freeze_until():
    """Nothing before the instant may change. A step that starts service
    before it must be one the plan already contained."""
    # The first stop is served at t=600, so a horizon of 100 freezes nothing
    # and the plan is free. (An earlier draft wrote `assert ... or True` here,
    # which is a tautology and tested precisely nothing.)
    p = problem((Lock(kind="FREEZE_UNTIL", instant=100),))
    assert not violations_of(verify(p, plan({"V1": ["O1"]}, p)))

    # Pinning the stop makes it committed work, which a freeze permits.
    pinned = problem((Lock(kind="FREEZE_UNTIL", instant=10 ** 6),
                      Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1",
                           vehicle_id="V1")))
    assert not violations_of(verify(pinned, plan({"V1": ["O1"]}, pinned)))

    frozen = Lock(kind="FREEZE_UNTIL", instant=10 ** 6)
    q = problem((frozen,))
    assert violations_of(verify(q, plan({"V1": ["O1", "O2"]}, q))), \
        "the whole plan sits inside the frozen window and is not marked frozen"


# --------------------------------------------------------------------------
# Model validation
# --------------------------------------------------------------------------

def test_an_unknown_lock_kind_is_refused():
    with pytest.raises(Exception, match="lock kind"):
        Lock(kind="PIN_ORDER_TO_MOOD", order_id="O1", vehicle_id="V1")


def test_a_lock_missing_its_subject_is_refused():
    """A PIN with no vehicle pins nothing, and would pass every check."""
    with pytest.raises(Exception, match="vehicle_id"):
        Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1")
    with pytest.raises(Exception, match="order_id"):
        Lock(kind="PIN_ORDER_TO_VEHICLE", vehicle_id="V1")
    with pytest.raises(Exception, match="order_ids"):
        Lock(kind="FIX_ROUTE_PREFIX", vehicle_id="V1")
    with pytest.raises(Exception, match="instant"):
        Lock(kind="FREEZE_UNTIL")


def test_a_lock_naming_something_absent_is_refused_by_the_problem():
    """A lock on an order that does not exist is a typo, and silently ignoring
    it is how an operator's instruction disappears."""
    with pytest.raises(Exception, match="unknown order"):
        problem((Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="NOPE",
                      vehicle_id="V1"),))
    with pytest.raises(Exception, match="unknown vehicle"):
        problem((Lock(kind="FORCE_DEPLOY", vehicle_id="V9"),))
