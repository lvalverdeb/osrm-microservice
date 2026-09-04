"""E-93 (T-93) — window attainment: was the promise kept, priced or not.

SDD §6.2, AC-4.2. The evaluator already costs a breached SOFT window
(`soft_penalties`) and reports it as `lateness_penalty`. That number answers
"what did lateness cost", which is not the question an SLA asks. A HARD window
carries no rate at all -- `TimeWindow.__post_init__` forbids one -- and a SOFT
window may carry a rate of zero, so in both cases a stop served hours after its
window closed is currently accounted at exactly zero lateness.

This measure is the unpriced companion: how many stops that were promised a
window were served inside one, and by how much the rest missed.
"""

from __future__ import annotations

from dataclasses import replace

from vrp.evaluator import build_timeline, route_metrics, window_attainment
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

ALL_DAY = (TimeWindow(start=0, end=100_000),)


def problem(b_windows: tuple[TimeWindow, ...] = ALL_DAY,
            a_windows: tuple[TimeWindow, ...] = ALL_DAY) -> Problem:
    """Depot at 0, A at 1, B at 2. D->A 300s, A->B 600s, B->D 900s.

    A is served 120s from 300; B is reached at 1020 and its window decides
    when service actually begins.
    """
    locations = (
        Location(id="D", lat=9.94, lon=-84.05, matrix_index=0),
        Location(id="A", lat=9.95, lon=-84.06, matrix_index=1),
        Location(id="B", lat=9.96, lon=-84.07, matrix_index=2),
    )
    durations = ((0, 300, 800), (300, 0, 600), (900, 600, 0))
    distances = ((0, 5000, 12000), (5000, 0, 9000), (14000, 9000, 0))
    orders = (
        Order(id="OA", kind="JOB", quantities={"weight": 10},
              delivery=StopSpec(location_id="A", time_windows=a_windows,
                                service_fixed=120)),
        Order(id="OB", kind="JOB", quantities={"weight": 4},
              delivery=StopSpec(location_id="B", time_windows=b_windows,
                                service_fixed=60)),
    )
    vehicles = (Vehicle(id="V1", capacities={"weight": 50},
                        shift=TimeWindow(start=0, end=86400),
                        start_location_id="D", end_location_id="D"),)
    return Problem(id="P", locations=locations, orders=orders, vehicles=vehicles,
                   matrix=TravelMatrix(version="m1", durations=durations,
                                       distances=distances))


def timeline_of(instance: Problem):
    return build_timeline(instance, "V1", ["OA", "OB"], start_time=0)


def test_a_missed_hard_window_costs_nothing_and_is_still_a_miss():
    """The gap this measure exists to close.

    B arrives at 1020 against a window that shut at 900. A HARD window cannot
    carry a lateness rate, so `lateness_penalty` is 0 and always will be --
    every existing report of lateness in the codebase is blind to this stop.
    """
    instance = problem(b_windows=(TimeWindow(start=0, end=900),))
    timeline = timeline_of(instance)

    assert route_metrics(instance, timeline)["lateness_penalty"] == 0

    attained = window_attainment(instance, timeline)
    assert attained.promised == 2
    assert attained.on_time == 1
    assert attained.missed == 1
    assert attained.lateness_seconds == 120
    assert attained.worst_lateness == 120


def test_a_soft_window_priced_at_zero_is_measured_all_the_same():
    """A rate of zero is a pricing decision, not evidence of punctuality."""
    instance = problem(b_windows=(TimeWindow(start=0, end=900, hardness="SOFT"),))
    timeline = timeline_of(instance)

    assert route_metrics(instance, timeline)["lateness_penalty"] == 0
    assert window_attainment(instance, timeline).lateness_seconds == 120


def test_service_inside_a_later_window_is_on_time_though_an_earlier_one_closed():
    """Disjoint windows, FR-04. Waiting for the second window keeps the promise."""
    instance = problem(b_windows=(TimeWindow(start=0, end=900),
                                  TimeWindow(start=3000, end=4000)))
    timeline = timeline_of(instance)

    # Arrival is 1020; the builder waits for the 3000 window.
    assert timeline[2].start_service == 3000
    attained = window_attainment(instance, timeline)
    assert attained.on_time == 2
    assert attained.lateness_seconds == 0


def test_lateness_is_measured_against_the_last_window_that_closed():
    """Missing every window of two is missing the later one, not the first.

    Charging the 900 window would report 120s against a promise the 1010 one
    superseded, which overstates the breach by an order of magnitude.
    """
    instance = problem(b_windows=(TimeWindow(start=0, end=900),
                                  TimeWindow(start=1000, end=1010)))
    timeline = timeline_of(instance)

    assert timeline[2].start_service == 1020
    assert window_attainment(instance, timeline).lateness_seconds == 10


def test_serving_before_the_window_opens_is_a_miss_with_no_lateness():
    """Early is a broken promise too, and not a late one.

    `build_timeline` waits for a window that has yet to open, so it cannot
    produce this. Steps reaching this measure do not all come from it --
    `vrp.triggers` passes the steps of a submitted `Solution` -- and a solver
    that ignored a window start would otherwise be scored punctual for it.
    """
    instance = problem(b_windows=(TimeWindow(start=3000, end=4000),))
    waited = timeline_of(instance)
    assert waited[2].start_service == 3000
    assert window_attainment(instance, waited).on_time == 2

    # The same stop served on arrival instead, an hour and a half early.
    early = waited[:2] + (replace(waited[2], start_service=1020),) + waited[3:]
    attained = window_attainment(instance, early)
    assert attained.on_time == 1
    assert attained.missed == 1
    assert attained.lateness_seconds == 0


def test_a_stop_promised_nothing_cannot_attain_or_miss():
    """No window is no promise, and counting it as kept inflates the rate.

    An unwindowed stop reads as unconstrained everywhere else in the model;
    scoring it as punctual would let a plan of windowless orders report
    perfect service.
    """
    instance = problem(a_windows=())
    attained = window_attainment(instance, timeline_of(instance))

    assert attained.promised == 1
    assert attained.on_time == 1
    assert attained.attained_ppt == 1000


def test_the_rate_is_parts_per_thousand_and_full_when_nothing_was_promised():
    """Matching `vrp.scenarios`, which reports service levels the same way."""
    late = TimeWindow(start=0, end=900)
    instance = problem(a_windows=(late,), b_windows=(late,))
    assert window_attainment(instance, timeline_of(instance)).attained_ppt == 500

    nothing = problem(a_windows=(), b_windows=())
    assert window_attainment(nothing, timeline_of(nothing)).attained_ppt == 1000
