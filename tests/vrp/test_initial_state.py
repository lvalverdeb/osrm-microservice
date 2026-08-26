"""Tachograph and ELD carry-over — §6.4, AC-5.2, T-26, E-26.

AC-5.2: "Where a driver's already-consumed hours are supplied as input,
planning respects the remaining legal envelope rather than assuming a fresh
duty." §6.4 is blunter: planning a fresh nine-hour duty for a driver who
already drove six "is a compliance incident, not an optimisation gap".

E-25 built the mechanism — `Vehicle.initial_state`, and an engine that honours
it. What was missing is the input: nothing turned a driver's actual recorded
day into that state, so `initial_state` was a number somebody had to work out
by hand and type in. §6.4 names the authority: "Where tachograph or ELD data is
available, it is the authoritative source for `initial_state`."

The hard part is not summing durations. It is knowing **which records belong to
the current duty**. A driver who drove nine hours yesterday, slept eleven, and
has driven two this morning has two hours consumed, not eleven — and a reader
that adds up everything it was handed would refuse to plan a legal day. The
rest that separates the two is the whole question, and it is a per-rule-set one:
EU-561 wants 11 hours, US-HOS wants 10.

Records are folded through the rule set's own `advance()` rather than counted
here, so there is exactly one implementation of what a DRIVE record does to a
driver.
"""

from __future__ import annotations

import pytest

from vrp.hos import EU_561, US_HOS, DriverState
from vrp.hos.tachograph import DutyRecord, read_duty, resume_from

HOUR = 3600


def record(activity: str, hours: float, at: int) -> DutyRecord:
    return DutyRecord(activity=activity, start=at, end=at + round(hours * HOUR))


def test_an_empty_record_set_is_a_rested_driver():
    """No data is not the same as no hours, but it is the only safe reading:
    §6.4 makes tachograph data authoritative *where available*."""
    assert read_duty((), EU_561) == DriverState()


def test_driving_records_accumulate_into_the_state():
    day = (record("DRIVE", 2, at=6 * HOUR),
           record("WORK", 1, at=8 * HOUR),
           record("DRIVE", 1.5, at=9 * HOUR))
    state = read_duty(day, EU_561)

    assert state.drive_used == round(3.5 * HOUR)
    assert state.duty_used == round(4.5 * HOUR)


def test_a_break_resets_the_driving_block_but_not_the_day():
    """The same rule E-25 pins, reached through recorded data rather than
    through calls: a break restores the 4.5h window, not the 9h day."""
    day = (record("DRIVE", 4.5, at=6 * HOUR),
           record("BREAK", 0.75, at=round(10.5 * HOUR)),
           record("DRIVE", 1, at=round(11.25 * HOUR)))
    state = read_duty(day, EU_561)

    assert state.since_last_break == HOUR
    assert state.drive_used == round(5.5 * HOUR)


# --------------------------------------------------------------------------
# Which records belong to *this* duty
# --------------------------------------------------------------------------

def test_a_daily_rest_starts_a_new_duty():
    """The question this module exists to answer.

    Nine hours driven yesterday, eleven hours rest, two hours this morning.
    A reader that summed everything would report eleven hours consumed and
    refuse to plan a day that is entirely legal.
    """
    history = (record("DRIVE", 9, at=6 * HOUR),
               record("REST", 11, at=15 * HOUR),
               record("DRIVE", 2, at=26 * HOUR))
    state = read_duty(history, EU_561)

    assert state.drive_used == 2 * HOUR, "yesterday's driving was carried over"
    assert EU_561.remaining_drive(state) == 7 * HOUR


def test_a_rest_too_short_to_qualify_does_not_start_a_new_duty():
    """Ten hours is a daily rest under US-HOS and is not one under EU-561.
    Treating any long gap as a reset would hand a driver a fresh day they have
    not legally earned, which is the failure direction that matters."""
    history = (record("DRIVE", 6, at=6 * HOUR),
               record("REST", 10, at=12 * HOUR),
               record("DRIVE", 1, at=22 * HOUR))

    eu = read_duty(history, EU_561)
    assert eu.drive_used == 7 * HOUR, "EU-561 needs 11h; this rest was 10"

    us = read_duty(history, US_HOS)
    assert us.drive_used == 1 * HOUR, "US-HOS needs 10h; this rest qualifies"


def test_only_the_most_recent_duty_survives():
    """Several days of history, and only the last one counts."""
    history = (record("DRIVE", 8, at=0),
               record("REST", 12, at=8 * HOUR),
               record("DRIVE", 7, at=20 * HOUR),
               record("REST", 12, at=27 * HOUR),
               record("DRIVE", 3, at=39 * HOUR))
    assert read_duty(history, EU_561).drive_used == 3 * HOUR


def test_the_week_total_survives_the_daily_reset():
    """A daily rest ends the day, not the week. EU-561's 56h weekly limit is
    not planned (E-25's scope note), but the consumption is carried so a future
    rule set can refuse against it -- dropping it here would lose the data."""
    history = (record("DRIVE", 9, at=6 * HOUR),
               record("REST", 11, at=15 * HOUR),
               record("DRIVE", 2, at=26 * HOUR))
    state = read_duty(history, EU_561)

    assert state.drive_used == 2 * HOUR
    assert state.week_drive_used == 11 * HOUR, "the week keeps accumulating"


# --------------------------------------------------------------------------
# Bad input, which is what real ELD feeds are made of
# --------------------------------------------------------------------------

def test_records_must_be_in_order():
    """An out-of-order feed is a broken feed. Sorting it silently would invent
    a duty the driver never worked."""
    with pytest.raises(ValueError, match="order"):
        read_duty((record("DRIVE", 2, at=10 * HOUR),
                   record("DRIVE", 1, at=6 * HOUR)), EU_561)


def test_overlapping_records_are_refused():
    """Two activities at once is a device fault, and picking one would be a
    guess about which."""
    with pytest.raises(ValueError, match="overlap"):
        read_duty((record("DRIVE", 3, at=6 * HOUR),
                   record("WORK", 1, at=8 * HOUR)), EU_561)


def test_an_unknown_activity_is_refused():
    """The vocabulary is the rules engine's. A status this does not understand
    cannot be silently treated as off-duty, which is the reading that would
    most flatter the plan."""
    with pytest.raises(ValueError, match="unknown activity"):
        read_duty((DutyRecord(activity="TEAPOT", start=0, end=HOUR),), EU_561)


def test_a_record_ending_before_it_starts_is_refused():
    with pytest.raises(ValueError, match="ends before"):
        read_duty((DutyRecord(activity="DRIVE", start=HOUR, end=0),), EU_561)


# --------------------------------------------------------------------------
# End to end: does planning respect it (AC-5.2)
# --------------------------------------------------------------------------

def test_a_vehicle_can_be_resumed_from_a_driver_s_recorded_day():
    """The convenience that makes this usable: records in, ready-to-plan
    vehicle out, with the carry-over already on it."""
    from vrp.model import TimeWindow, Vehicle

    van = Vehicle(id="V1", capacities={"kg": 100},
                  shift=TimeWindow(start=0, end=24 * HOUR),
                  start_location_id="D", end_location_id="D",
                  hos_rules="EU-561")
    history = (record("DRIVE", 6, at=6 * HOUR),)

    resumed = resume_from(van, history)
    assert resumed.initial_state.drive_used == 6 * HOUR
    assert EU_561.remaining_drive(resumed.initial_state) == 3 * HOUR


def test_planning_respects_the_remaining_envelope():
    """AC-5.2 itself, through the scheduler.

    The same round is legal for a rested driver and not for one six hours in.
    E-25 proved the scheduler honours `initial_state`; this proves the number
    reaching it came from the driver's actual recorded day.
    """
    from vrp.hos.schedule import schedule_route
    from vrp.model import (
        Location,
        Order,
        Problem,
        StopSpec,
        TimeWindow,
        TravelMatrix,
        Vehicle,
    )

    day = TimeWindow(start=0, end=24 * HOUR)
    leg = 2 * HOUR
    locations = (Location(id="D", lat=9.9, lon=-84.0, matrix_index=0),
                 Location(id="C1", lat=9.91, lon=-84.0, matrix_index=1))
    grid = ((0, leg), (leg, 0))
    van = Vehicle(id="V1", capacities={"kg": 100}, shift=day,
                  start_location_id="D", end_location_id="D",
                  hos_rules="EU-561")

    def plan_with(vehicle: Vehicle) -> bool:
        problem = Problem(
            id="ac52", locations=locations, vehicles=(vehicle,),
            orders=(Order(id="O1", kind="JOB", quantities={"kg": 1},
                          delivery=StopSpec(location_id="C1",
                                            time_windows=(day,),
                                            service_fixed=60)),),
            matrix=TravelMatrix(version="v", durations=grid, distances=grid))
        return schedule_route(problem, "V1", ["O1"], EU_561).legal

    assert plan_with(van), "four hours of driving is a legal day when rested"
    tired = resume_from(van, (record("DRIVE", 8, at=0),))
    assert not plan_with(tired), (
        "a driver eight hours in cannot legally add four more")


def test_resuming_a_vehicle_with_no_rule_set_is_refused():
    """Carry-over is meaningless without a rule set to interpret it against --
    "six hours consumed" is only a constraint relative to a limit."""
    from vrp.model import TimeWindow, Vehicle

    van = Vehicle(id="V1", capacities={}, shift=TimeWindow(0, HOUR),
                  start_location_id="D")
    with pytest.raises(ValueError, match="hos_rules"):
        resume_from(van, (record("DRIVE", 1, at=0),))
