"""Pre-flight rejection reasons — §6.5, FR-01, AC-1.3, T-14, E-14.

§6.5 closes with the requirement that shapes this whole module: "Each reason
MUST be produced by an explicit diagnostic pass, not inferred." A solver that
fails to place an order knows only that it failed; turning that into
"CAPACITY_EXCEEDED" by guessing is how a dispatcher gets told the wrong thing
confidently.

So these checks run *before* any solve and answer a narrower question: is this
order servable by any vehicle at all, ignoring every other order? That is what
makes an answer trustworthy. An order that passes pre-flight may still go
unassigned because the fleet ran out — a different code, and one that needs a
solve to establish.

Six of §6.5's ten codes are decidable pre-flight and are implemented.
`test_the_unimplementable_codes_are_named_rather_than_silently_absent` pins the
other four with their reasons, so the gap is visible in the test output rather
than discovered by someone waiting for a code that never comes.

Precedence matters and is tested. An order can fail several ways at once, and
reporting the incidental one sends the dispatcher to fix the wrong thing.
"""

from __future__ import annotations

from vrp.diagnose import REASONS, UNIMPLEMENTED, preflight
from vrp.model import (
    Location,
    Lock,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

DAY = TimeWindow(start=0, end=12 * 3600)


def instance(order: Order, vehicles: tuple[Vehicle, ...],
             locks: tuple[Lock, ...] = (), leg: int = 600,
             site: Location | None = None) -> Problem:
    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                 site or Location(id="C1", lat=9.91, lon=-84.0,
                                  matrix_index=1))
    grid = ((0, leg), (leg, 0))
    return Problem(id="pf", locations=locations, orders=(order,),
                   vehicles=vehicles, locks=locks,
                   matrix=TravelMatrix(version="pf", durations=grid,
                                       distances=grid))


def an_order(**kwargs) -> Order:
    stop = StopSpec(location_id="C1",
                    time_windows=kwargs.pop("windows", (DAY,)),
                    service_fixed=kwargs.pop("service", 60))
    return Order(id="O1", kind="JOB",
                 quantities=kwargs.pop("quantities", {"kg": 1}),
                 delivery=stop, **kwargs)


def a_van(**kwargs) -> Vehicle:
    defaults = {"capacities": {"kg": 100}, "shift": DAY,
                "start_location_id": "D", "end_location_id": "D"}
    return Vehicle(id=kwargs.pop("id", "V1"), **{**defaults, **kwargs})


def reason_for(problem: Problem) -> str | None:
    found = preflight(problem)
    return found["O1"].code if "O1" in found else None


def detail_for(problem: Problem) -> str:
    return preflight(problem)["O1"].detail


def restricted(**access) -> Location:
    return Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1, **access)


def test_a_servable_order_is_not_reported():
    """The control. A diagnostic pass that flags everything is useless, and
    would make every test below pass for the wrong reason."""
    assert preflight(instance(an_order(), (a_van(),))) == {}


def test_no_eligible_vehicle_when_no_one_has_the_skill():
    """§6.5's vehicle↔order compatibility: tail-lift, refrigeration, ADR."""
    problem = instance(an_order(required_skills=frozenset({"TAIL_LIFT"})),
                       (a_van(),))
    assert reason_for(problem) == "NO_ELIGIBLE_VEHICLE"

    equipped = instance(an_order(required_skills=frozenset({"TAIL_LIFT"})),
                        (a_van(skills=frozenset({"TAIL_LIFT"})),))
    assert reason_for(equipped) is None


def test_the_reason_names_the_constraint_that_disqualified_the_fleet():
    """NO_ELIGIBLE_VEHICLE covers skills *and* site access, and `_eligible`
    filters on both. Phrasing every case in terms of skills sends a dispatcher
    to find a tail lift when the real problem is that a 7.5-tonne lorry may not
    enter the street -- and §6.5's whole point is that the reason must be
    produced by an explicit diagnostic pass, not inferred.

    The code was already right in all three cases. Only the sentence was wrong,
    which is the more dangerous failure: a wrong code is caught by anything
    branching on it, and a wrong sentence is read by a person who then acts.
    """
    skills = instance(an_order(required_skills=frozenset({"TAIL_LIFT"})),
                      (a_van(),))
    assert reason_for(skills) == "NO_ELIGIBLE_VEHICLE"
    assert "TAIL_LIFT" in detail_for(skills), detail_for(skills)

    zone = instance(an_order(), (a_van(access_class="RIGID"),),
                    site=restricted(access_classes=frozenset({"BIKE"})))
    assert reason_for(zone) == "NO_ELIGIBLE_VEHICLE"
    assert "skills" not in detail_for(zone), detail_for(zone)
    assert "BIKE" in detail_for(zone), detail_for(zone)

    bridge = instance(an_order(), (a_van(gross_weight_kg=7_500),),
                      site=restricted(max_vehicle_kg=3_500))
    assert reason_for(bridge) == "NO_ELIGIBLE_VEHICLE"
    assert "skills" not in detail_for(bridge), detail_for(bridge)
    assert "3500" in detail_for(bridge), detail_for(bridge)


def test_a_skilless_order_no_vehicle_can_reach_does_not_claim_a_skill_problem():
    """The exact sentence the E-22 example printed: an order requiring nothing
    at all, reported as "requires no skills; no vehicle qualifies"."""
    zone = instance(an_order(), (a_van(access_class="RIGID"),),
                    site=restricted(access_classes=frozenset({"BIKE"})))

    assert "requires no skills" not in detail_for(zone), detail_for(zone)


def test_a_fleet_emptied_by_a_lock_does_not_blame_skills_or_the_site():
    """When a pin removed the alternatives, neither the skill list nor the site
    is the thing to report -- `report` already appends the lock, and the detail
    should not also name a constraint that was never violated."""
    problem = instance(an_order(), (a_van(), a_van(id="V2")),
                       locks=(Lock(kind="FORBID_DEPLOY", vehicle_id="V1"),
                              Lock(kind="FORBID_DEPLOY", vehicle_id="V2")))

    detail = detail_for(problem)
    assert "skills" not in detail, detail
    assert "admits" not in detail, detail


def test_capacity_exceeded_when_the_order_alone_is_too_big():
    """"The order *alone* exceeds every eligible vehicle's capacity" -- which
    is what makes it pre-flight. An order that only overflows alongside others
    is FLEET_EXHAUSTED and needs a solve to establish."""
    problem = instance(an_order(quantities={"kg": 500}), (a_van(),))
    assert reason_for(problem) == "CAPACITY_EXCEEDED"


def test_capacity_is_checked_on_every_dimension():
    """A van full by volume is full. Checking only the first dimension would
    pass this and load the cube past the roof."""
    order = an_order(quantities={"kg": 1, "m3": 50})
    problem = instance(order, (a_van(capacities={"kg": 100, "m3": 10}),))
    assert reason_for(problem) == "CAPACITY_EXCEEDED"


def test_time_window_unreachable_when_no_vehicle_can_arrive_in_time():
    """The stop is 600 s away and the window shuts at 100."""
    problem = instance(an_order(windows=(TimeWindow(start=0, end=100),)),
                       (a_van(),))
    assert reason_for(problem) == "TIME_WINDOW_UNREACHABLE"


def test_a_soft_window_is_not_unreachable():
    """A soft window is a preference, not a wall -- arriving late is allowed
    and priced (E-23). Reporting it as unreachable would refuse work the
    business is happy to do."""
    soft = TimeWindow(start=0, end=100, hardness="SOFT",
                      lateness_cost_per_sec=1)
    assert reason_for(instance(an_order(windows=(soft,)), (a_van(),))) is None


def test_release_after_window_when_the_goods_arrive_too_late():
    """FR-06. Distinct from TIME_WINDOW_UNREACHABLE: the vehicle could get
    there in time, but the goods will not exist yet -- and the fix is a
    conversation with the warehouse, not with the fleet."""
    order = an_order(windows=(TimeWindow(start=0, end=3_600),),
                     release_time=7_200)
    assert reason_for(instance(order, (a_van(),))) == "RELEASE_AFTER_WINDOW"


def test_duty_limit_when_the_round_trip_cannot_fit_a_legal_duty():
    """E-25's hours of service, applied before the solve. A 7-hour leg each way
    is 14 hours of driving, which no shipped rule set permits."""
    problem = instance(an_order(), (a_van(hos_rules="EU-561"),),
                       leg=7 * 3600)
    assert reason_for(problem) == "DUTY_LIMIT"


def test_lock_conflict_when_the_pinned_vehicle_cannot_serve_it():
    """An operator pinned the order to a van that is too small for it. §6.6
    says a lock is never silently dropped, so the answer is a conflict, not a
    quiet reassignment."""
    order = an_order(quantities={"kg": 500})
    problem = instance(
        order,
        (a_van(id="V1", capacities={"kg": 100}),
         a_van(id="V2", capacities={"kg": 1_000})),
        locks=(Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1",
                    vehicle_id="V1"),))
    assert reason_for(problem) == "LOCK_CONFLICT"


def test_a_pin_to_a_capable_vehicle_is_no_conflict():
    order = an_order(quantities={"kg": 500})
    problem = instance(
        order, (a_van(id="V1", capacities={"kg": 1_000}),),
        locks=(Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="O1",
                    vehicle_id="V1"),))
    assert reason_for(problem) is None


# --------------------------------------------------------------------------
# Precedence and honesty about scope
# --------------------------------------------------------------------------

def test_the_most_fundamental_reason_wins():
    """An order can fail several ways at once.

    This one needs a skill nobody has *and* is too big for anything. Reporting
    the capacity would send someone to find a bigger van, which would still not
    have a tail lift. Eligibility is decided first because it determines which
    vehicles the later checks even consider.
    """
    order = an_order(quantities={"kg": 500},
                     required_skills=frozenset({"TAIL_LIFT"}))
    assert reason_for(instance(order, (a_van(),))) == "NO_ELIGIBLE_VEHICLE"


def test_capacity_outranks_the_time_window():
    """Both fail, and capacity is the one that no amount of rescheduling fixes."""
    order = an_order(quantities={"kg": 500},
                     windows=(TimeWindow(start=0, end=100),))
    assert reason_for(instance(order, (a_van(),))) == "CAPACITY_EXCEEDED"


def test_every_reported_code_is_in_the_closed_vocabulary():
    """§6.5 calls it "the closed vocabulary emitted for unassigned orders", so
    a code invented here would be one no consumer can handle."""
    cases = (
        instance(an_order(required_skills=frozenset({"X"})), (a_van(),)),
        instance(an_order(quantities={"kg": 500}), (a_van(),)),
        instance(an_order(windows=(TimeWindow(start=0, end=100),)), (a_van(),)),
    )
    for problem in cases:
        for finding in preflight(problem).values():
            assert finding.code in REASONS, finding.code


def test_the_unimplementable_codes_are_named_rather_than_silently_absent():
    """The codes that cannot be decided here, named rather than omitted.

    Two need a solve -- a pre-flight pass cannot know the fleet ran out -- and
    one needs depot inventory. INCOMPATIBLE_ONLY was among them until E-22
    modelled order-to-order classes, which is why this set shrinks rather than
    being a fixed list.
    """
    # INCOMPATIBLE_ONLY left this set with E-22, which modelled order-to-order
    # incompatibility. The two that remain need a solve; the third needs depot
    # inventory.
    assert set(UNIMPLEMENTED) == {
        "FLEET_EXHAUSTED", "DROPPED_BY_PRIZE", "DEPOT_STOCKOUT"}
    assert all(why for why in UNIMPLEMENTED.values()), "each needs a reason"
    assert set(UNIMPLEMENTED) <= set(REASONS), "and each must be a real code"


def test_every_reason_is_documented():
    """The vocabulary and the docstrings stay together, so a code cannot be
    added without saying what it means."""
    assert len(REASONS) == 10, "§6.5 defines ten"
    assert all(text.strip() for text in REASONS.values())


def test_the_adapter_names_the_order_when_a_release_makes_it_unsolvable():
    """A Problem our model accepts must not die inside PyVRP.

    `release_time` after `tw_late` is infeasible, not malformed, so the model
    is right to accept it and `preflight` is right to call it
    RELEASE_AFTER_WINDOW. PyVRP disagrees at construction and raises
    "release_time must be <= tw_late" from inside Client(), naming no order --
    found by running the E-14 example over a seeded instance.
    """
    import pytest

    from vrp.solve.pyvrp_adapter import solve

    order = an_order(windows=(TimeWindow(start=0, end=3_600),),
                     release_time=7_200)
    problem = instance(order, (a_van(),))

    assert reason_for(problem) == "RELEASE_AFTER_WINDOW"
    with pytest.raises(ValueError, match="O1.*RELEASE_AFTER_WINDOW"):
        solve(problem, iterations=10, seed=0)
