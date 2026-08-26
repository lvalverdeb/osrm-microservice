"""Hours-of-service rules engine — SDD §6.4, FR-15, FR-16, T-25 [GATE].

These assertions come from the regulation text quoted in §6.4, not from the
implementation. That direction matters more here than anywhere else in the
suite: §6.4 opens by pointing out that working-time law carries criminal and
licensing consequences, so a test that merely agrees with the code would be
worse than no test.

The last group is the gate condition. §6.4 requires break insertion to be a
scheduling subproblem *inside* route evaluation, and names the symptom of
getting it wrong: a plan that was feasible before breaks and loses its last two
stops per route on publication. `test_breaks_are_placed_during_evaluation_...`
is what distinguishes the two.
"""

from __future__ import annotations

import pytest

from vrp.hos import EU_561, US_HOS, Activity, DriverState, rules_for
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

HOUR = 3600


# --------------------------------------------------------------------------
# EU 561/2006
# --------------------------------------------------------------------------

def test_eu_561_requires_a_45_minute_break_after_four_and_a_half_hours_driving():
    """"A break of at least 45 min after at most 4.5 h of accumulated driving"."""
    state = EU_561.init_state()
    state = EU_561.advance(state, Activity.DRIVE, int(4.5 * HOUR))

    assert not EU_561.can_drive(state, 1), "driving past 4.5h without a break"
    required = EU_561.required_break(state)
    assert required is not None
    assert required.duration == 45 * 60
    assert "561" in required.rule_ref and "7" in required.rule_ref


def test_eu_561_allows_driving_right_up_to_the_limit():
    """The limit is "at most 4.5 h", so 4.5h itself is legal -- an off-by-one
    here would insert a break a second early on every route in the fleet."""
    state = EU_561.init_state()
    state = EU_561.advance(state, Activity.DRIVE, int(4.5 * HOUR) - 1)
    assert EU_561.can_drive(state, 1)
    assert EU_561.required_break(state) is None


def test_eu_561_break_resets_the_driving_accumulator_but_not_the_daily_total():
    """A break restores the 4.5h window; it does not give back the 9h day."""
    state = EU_561.init_state()
    state = EU_561.advance(state, Activity.DRIVE, int(4.5 * HOUR))
    state = EU_561.advance(state, Activity.BREAK, 45 * 60)

    assert EU_561.can_drive(state, HOUR)
    assert state.since_last_break == 0
    assert state.drive_used == int(4.5 * HOUR)


def test_eu_561_enforces_the_nine_hour_daily_driving_limit():
    """"Daily driving limit 9 h." Extension to 10h is twice a week and needs a
    week-long horizon, so it is not planned here -- see the module note."""
    state = EU_561.init_state()
    # Drive the full day legally, breaking when told to.
    for _ in range(2):
        state = EU_561.advance(state, Activity.DRIVE, int(4.5 * HOUR))
        state = EU_561.advance(state, Activity.BREAK, 45 * 60)
    assert state.drive_used == 9 * HOUR
    assert not EU_561.can_drive(state, 1), "drove past the 9h daily limit"
    assert EU_561.remaining_drive(state) == 0


# --------------------------------------------------------------------------
# US FMCSA HOS
# --------------------------------------------------------------------------

def test_us_hos_requires_a_30_minute_break_after_eight_hours_driving():
    """"A 30-minute interruption of driving required after 8 cumulative hours"."""
    state = US_HOS.init_state()
    state = US_HOS.advance(state, Activity.DRIVE, 8 * HOUR)

    assert not US_HOS.can_drive(state, 1)
    required = US_HOS.required_break(state)
    assert required is not None
    assert required.duration == 30 * 60


def test_us_hos_enforces_eleven_hours_driving():
    """"11 h driving within a 14 h duty window"."""
    state = US_HOS.init_state()
    state = US_HOS.advance(state, Activity.DRIVE, 8 * HOUR)
    state = US_HOS.advance(state, Activity.BREAK, 30 * 60)
    state = US_HOS.advance(state, Activity.DRIVE, 3 * HOUR)

    assert state.drive_used == 11 * HOUR
    assert not US_HOS.can_drive(state, 1), "drove past the 11h limit"


def test_us_hos_duty_window_closes_even_when_driving_hours_remain():
    """The 14h window is wall-clock from coming on duty: waiting and working
    consume it, driving hours do not restore it. A driver can be out of window
    with driving time left, which is the case most easily got wrong."""
    state = US_HOS.init_state()
    state = US_HOS.advance(state, Activity.DRIVE, 4 * HOUR)
    state = US_HOS.advance(state, Activity.WORK, 6 * HOUR)      # loading
    state = US_HOS.advance(state, Activity.WAIT, 4 * HOUR)      # at a dock

    assert state.drive_used == 4 * HOUR, "still has driving hours in hand"
    assert US_HOS.remaining_drive(state) == 0, "but the 14h window has closed"
    assert not US_HOS.can_drive(state, 1)


def test_the_two_rule_sets_disagree_which_is_why_the_engine_is_pluggable():
    """Six hours of driving is legal in the US and needs a break in the EU."""
    for rules, needs_break in ((EU_561, True), (US_HOS, False)):
        state = rules.advance(rules.init_state(), Activity.DRIVE, 6 * HOUR)
        assert (rules.required_break(state) is not None) is needs_break


def test_rule_sets_are_selectable_by_name():
    """FR-15 calls for a pluggable rule set, which means resolvable by name."""
    assert rules_for("EU-561") is EU_561
    assert rules_for("US-HOS") is US_HOS
    with pytest.raises(ValueError, match="unknown"):
        rules_for("EU-562")


# --------------------------------------------------------------------------
# initial_state carry-over (§6.4 "Mandatory input")
# --------------------------------------------------------------------------

def test_a_driver_who_already_drove_six_hours_gets_a_shorter_day():
    """§6.4: planning a fresh 9-hour duty for a driver who already drove 6 is
    "a compliance incident, not an optimisation gap"."""
    fresh = EU_561.init_state()
    partial = EU_561.init_state(DriverState(drive_used=6 * HOUR, duty_used=6 * HOUR,
                                            since_last_break=0))
    assert EU_561.remaining_drive(fresh) == 9 * HOUR
    assert EU_561.remaining_drive(partial) == 3 * HOUR


def test_carry_over_includes_time_since_the_last_break():
    """A driver 4 hours into a driving block gets 30 minutes, not 4.5 hours."""
    state = EU_561.init_state(DriverState(drive_used=4 * HOUR, duty_used=4 * HOUR,
                                          since_last_break=4 * HOUR))
    assert EU_561.can_drive(state, 30 * 60)
    assert not EU_561.can_drive(state, 30 * 60 + 1)


# --------------------------------------------------------------------------
# The gate: breaks inside evaluation, not after it
# --------------------------------------------------------------------------

def _long_haul(stops: int, leg_hours: float, shift_hours: int = 24) -> Problem:
    """A line-haul day: every leg is long enough to force breaks."""
    leg = int(leg_hours * HOUR)
    size = stops + 1
    shift = TimeWindow(start=0, end=shift_hours * HOUR)
    locations = [Location(id="DEPOT", lat=9.9, lon=-84.0, matrix_index=0)]
    orders = []
    for index in range(1, size):
        locations.append(Location(id=f"C{index}", lat=9.9 + index / 100,
                                  lon=-84.0, matrix_index=index))
        orders.append(Order(id=f"O{index}", kind="JOB", quantities={"units": 1},
                            delivery=StopSpec(location_id=f"C{index}",
                                              time_windows=(shift,),
                                              service_fixed=600)))
    durations = tuple(tuple(0 if i == j else leg for j in range(size))
                      for i in range(size))
    distances = tuple(tuple(0 if i == j else leg * 20 for j in range(size))
                      for i in range(size))
    return Problem(id="line-haul", locations=tuple(locations),
                   orders=tuple(orders),
                   vehicles=(Vehicle(id="V1", capacities={"units": 100},
                                     shift=shift, start_location_id="DEPOT",
                                     end_location_id="DEPOT"),),
                   matrix=TravelMatrix(version="lh", durations=durations,
                                       distances=distances))


def test_breaks_are_placed_during_evaluation_not_bolted_on_afterwards():
    """The gate condition, and the reason T-25 blocks its successors.

    Two hours of driving per leg means a break falls due partway through the
    route. A schedule built without breaks and patched afterwards would show
    the same arrival times with breaks appended; one built with breaks inside
    shows every arrival after the first break pushed later by its duration.
    """
    from vrp.hos.schedule import schedule_route

    problem = _long_haul(stops=4, leg_hours=2.0)
    timeline = schedule_route(problem, "V1", ["O1", "O2", "O3", "O4"], EU_561)

    breaks = [s for s in timeline.steps if s.type == "BREAK"]
    assert breaks, "4 legs x 2h = 8h of driving must force at least one break"

    first_break = timeline.steps.index(breaks[0])
    after = [s for s in timeline.steps[first_break + 1:] if s.type == "DELIVERY"]
    assert after, "the break must fall mid-route, not at the end"

    # Every stop after the break is displaced by the break, which is precisely
    # what a post-processing pass fails to do.
    without = schedule_route(problem, "V1", ["O1", "O2", "O3", "O4"], None)
    displaced = [s for s in after if any(
        w.order_id == s.order_id and s.arrival > w.arrival
        for w in without.steps)]
    assert len(displaced) == len(after), (
        "arrivals after a break were not pushed later -- breaks look bolted on")


def test_a_route_that_cannot_fit_a_legal_break_is_reported_not_silently_shortened():
    """§6.4's named failure: plans that "lose" their last stops on publication.

    Ten 2-hour legs is 20 hours of driving, which no daily rule set permits.
    The scheduler must say so rather than return a plan holding whatever fitted.
    """
    from vrp.hos.schedule import schedule_route

    problem = _long_haul(stops=10, leg_hours=2.0)
    order_ids = [f"O{n}" for n in range(1, 11)]
    timeline = schedule_route(problem, "V1", order_ids, EU_561)

    assert not timeline.legal, "20h of driving cannot be legal under EU-561"
    assert timeline.violation is not None
    served = {s.order_id for s in timeline.steps if s.order_id}
    assert served == set(order_ids), (
        "the scheduler dropped stops instead of reporting the violation")


def test_a_short_day_needs_no_breaks_and_stays_legal():
    """The control. Without this the two tests above pass on a scheduler that
    calls everything illegal."""
    from vrp.hos.schedule import schedule_route

    problem = _long_haul(stops=2, leg_hours=1.0)
    timeline = schedule_route(problem, "V1", ["O1", "O2"], EU_561)

    assert timeline.legal
    assert [s for s in timeline.steps if s.type == "BREAK"] == []


# --------------------------------------------------------------------------
# INV-7 under the independent verifier — the gate condition
# --------------------------------------------------------------------------

def _duty(rules_name: str, order_ids: list[str], steps: tuple,
          problem: Problem) -> Solution:
    return Solution(problem_id=problem.id,
                    routes=(Route(vehicle_id="V1", steps=steps),),
                    unassigned=(), objective_breakdown={}, status="FEASIBLE")


def _with_rules(problem: Problem, rules_name: str,
                initial: DriverState | None = None) -> Problem:
    """Same problem, with the fleet placed under a rule set."""
    vehicles = tuple(
        Vehicle(id=v.id, capacities=v.capacities, shift=v.shift,
                start_location_id=v.start_location_id,
                end_location_id=v.end_location_id, hos_rules=rules_name,
                initial_state=initial)
        for v in problem.vehicles
    )
    return Problem(id=problem.id, locations=problem.locations,
                   orders=problem.orders, vehicles=vehicles,
                   matrix=problem.matrix)


@pytest.mark.parametrize("rules_name", ["EU-561", "US-HOS"])
def test_a_scheduled_duty_is_legal_under_the_verifier(rules_name):
    """The gate: both shipped rule sets produce plans INV-7 accepts.

    The plan comes from the scheduler and the judgement from the verifier,
    which shares no code with it -- so this is two implementations of the same
    regulation agreeing, not one implementation agreeing with itself.
    """
    from vrp.hos.schedule import schedule_route

    # 5 legs x 1.5h = 7.5h driving: inside EU-561's 9h day and US-HOS's 11h,
    # but past the 4.5h that forces an EU break. 2h legs would be 10h and
    # genuinely illegal under EU-561 -- the first draft of this fixture was,
    # and the scheduler was right to reject it.
    problem = _with_rules(_long_haul(stops=4, leg_hours=1.5), rules_name)
    order_ids = ["O1", "O2", "O3", "O4"]
    timeline = schedule_route(problem, "V1", order_ids, rules_for(rules_name))
    assert timeline.legal, timeline.violation

    report = verify(problem, _duty(rules_name, order_ids, timeline.steps, problem))
    assert "INV-7" not in report.not_applicable, "INV-7 was not evaluated"
    assert report.ok, [str(v) for v in report.violations]


def test_the_verifier_catches_a_duty_that_skipped_its_break():
    """A plan with the breaks removed must be rejected, not merely re-timed.

    This is the fixture that matters. Deleting the BREAK steps leaves a
    timeline that is internally consistent -- every arrival still follows its
    departure -- and is illegal only against the regulation.
    """
    from vrp.hos.schedule import schedule_route

    problem = _with_rules(_long_haul(stops=4, leg_hours=2.0), "EU-561")
    timeline = schedule_route(problem, "V1", ["O1", "O2", "O3", "O4"], EU_561)
    assert any(s.type == "BREAK" for s in timeline.steps), "fixture needs a break"

    without_breaks = tuple(s for s in timeline.steps if s.type != "BREAK")
    report = verify(problem, _duty("EU-561", [], without_breaks, problem))

    assert not report.ok
    assert any(v.invariant == "INV-7" for v in report.violations), \
        [str(v) for v in report.violations]


def test_inv_7_is_not_applicable_when_no_vehicle_declares_a_rule_set():
    """Silence about hours is only honest when nobody claimed to be subject to
    them. Reporting "ok" here would be the lie the verifier's docstring warns of."""
    from vrp.hos.schedule import schedule_route

    problem = _long_haul(stops=2, leg_hours=1.0)          # no hos_rules
    timeline = schedule_route(problem, "V1", ["O1", "O2"], None)
    report = verify(problem, _duty("", [], timeline.steps, problem))
    assert "INV-7" in report.not_applicable


def test_carry_over_can_make_an_otherwise_legal_duty_illegal():
    """§6.4's compliance incident, as a test: the same plan, a tired driver."""
    from vrp.hos.schedule import schedule_route

    fresh = _with_rules(_long_haul(stops=3, leg_hours=2.0), "EU-561")
    timeline = schedule_route(fresh, "V1", ["O1", "O2", "O3"], EU_561)
    assert timeline.legal

    tired = _with_rules(_long_haul(stops=3, leg_hours=2.0), "EU-561",
                        initial=DriverState(drive_used=7 * HOUR,
                                            duty_used=7 * HOUR,
                                            since_last_break=0))
    report = verify(tired, _duty("EU-561", [], timeline.steps, tired))
    assert not report.ok, "a driver 7 hours in cannot legally repeat this duty"
    assert any(v.invariant == "INV-7" for v in report.violations)


def test_the_scheduler_honours_carry_over_declared_on_the_vehicle():
    """A driver's consumed hours live on the vehicle; the scheduler must read
    them without being told twice.

    The first version took `initial_state` only as an argument, so a `Problem`
    carrying an exhausted driver scheduled as though they were rested. Nothing
    raised -- the plan simply understated the driver's day, which is the exact
    compliance incident §6.4 describes. The verifier caught it by reading the
    vehicle while the scheduler read its own parameter.
    """
    from vrp.hos.schedule import schedule_route

    base = _long_haul(stops=3, leg_hours=2.0)
    rested = _with_rules(base, "EU-561")
    tired = _with_rules(base, "EU-561",
                        initial=DriverState(drive_used=7 * HOUR,
                                            duty_used=7 * HOUR))
    order_ids = ["O1", "O2", "O3"]

    assert schedule_route(rested, "V1", order_ids, EU_561).legal
    assert not schedule_route(tired, "V1", order_ids, EU_561).legal, (
        "scheduler ignored the carry-over declared on the vehicle")


def test_the_scheduler_reports_load_so_capacity_can_be_checked():
    """A timeline without `load_after` cannot be checked against capacity.

    INV-5 iterates the loads a step reports, so a step reporting none passes
    silently. The scheduler produced exactly that, which meant any plan routed
    through it was exempt from capacity checking without saying so.
    """
    from vrp.hos.schedule import schedule_route

    problem = _long_haul(stops=3, leg_hours=1.0)
    timeline = schedule_route(problem, "V1", ["O1", "O2", "O3"], EU_561)

    start = timeline.steps[0]
    assert start.load_after.get("units") == 3, "leaves the depot carrying all three"
    delivered = [s for s in timeline.steps if s.type == "DELIVERY"]
    assert [s.load_after["units"] for s in delivered] == [2, 1, 0]
    assert timeline.steps[-1].load_after.get("units") == 0
