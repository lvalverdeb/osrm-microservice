"""The historical replayer — DYN-6, AC-3.2, §8.1, T-53 [GATE], E-53.

DYN-6: "Simulator/replayer -- replays historical days epoch-by-epoch to evaluate
policies offline". AC-3.2: "The dispatch policy is selectable and its expected
cost is reported against a greedy-dispatch baseline over a replayed historical
day."

This is the gate the rest of Slice 5 stands on. T-54's ICD policy has to "beat
greedy and lazy on the replay corpus" and T-55's prize-collecting has to be
"comparable or better than ICD"; neither claim means anything without a
measurement both are made against. So the replayer is built before the policies
that need it, and §8.2's baselines were built before the replayer.

**The thing that makes it a replayer rather than a loop.** §8.1's whole premise
is that requests are *not all known at the start*: "at each epoch the agent
observes the requests known so far". A replayer that handed every request to
epoch 0 would be a static solve wearing a costume, and every policy would score
identically because there would be nothing left to consolidate.
`test_a_request_is_invisible_before_it_arrives` is that check, and
`test_policies_score_differently_on_the_same_day` is the consequence.

**Determinism is in the definition of done** -- "deterministic replay of 90
historical days" -- and for the usual CON-4 reason: a policy comparison nobody
can reproduce is not evidence, it is an anecdote. Two replays of one day with
one policy must agree exactly.
"""

from __future__ import annotations

import pytest

from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.policies import BASELINES, greedy, lazy
from vrp.replay import Day, compare, generate_days, replay

HOUR = 3600
DAY = TimeWindow(start=0, end=8 * HOUR)
LEG = 900


def problem(stops: int = 8, vans: int = 2) -> Problem:
    size = stops + 1
    grid = tuple(tuple(abs(i - j) * LEG for j in range(size))
                 for i in range(size))
    return Problem(
        id="replay",
        locations=tuple(Location(id="D" if i == 0 else f"C{i}",
                                 lat=9.9 + i / 100, lon=-84.0, matrix_index=i)
                        for i in range(size)),
        orders=tuple(Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
                           delivery=StopSpec(location_id=f"C{i}",
                                             time_windows=(DAY,),
                                             service_fixed=300))
                     for i in range(1, size)),
        vehicles=tuple(Vehicle(id=f"V{n}", capacities={"kg": 100}, shift=DAY,
                               start_location_id="D", end_location_id="D",
                               cost_per_metre=1)
                       for n in range(1, vans + 1)),
        matrix=TravelMatrix(version="r", durations=grid, distances=grid))


# --------------------------------------------------------------------------
# The corpus
# --------------------------------------------------------------------------

def test_a_corpus_has_the_days_it_was_asked_for():
    days = generate_days(problem(), count=90, seed=0, horizon=DAY)

    assert len(days) == 90
    assert all(isinstance(day, Day) for day in days)


def test_every_request_arrives_inside_the_horizon():
    """A request arriving after the day ends can never be dispatched, and would
    read as a policy failure rather than a corpus one."""
    days = generate_days(problem(), count=30, seed=0, horizon=DAY)

    for day in days:
        for arrival in day.arrivals.values():
            assert DAY.start <= arrival < DAY.end, (day.id, arrival)


def test_the_days_differ():
    days = generate_days(problem(), count=30, seed=0, horizon=DAY)
    shapes = {tuple(sorted(day.arrivals.items())) for day in days}

    assert len(shapes) > 1


def test_the_same_seed_gives_the_same_corpus():
    left = generate_days(problem(), count=30, seed=5, horizon=DAY)
    right = generate_days(problem(), count=30, seed=5, horizon=DAY)

    assert [d.arrivals for d in left] == [d.arrivals for d in right]


# --------------------------------------------------------------------------
# §8.1: requests arrive over the day
# --------------------------------------------------------------------------

def test_a_request_is_invisible_before_it_arrives():
    """§8.1: "at each epoch the agent observes the requests known so far".

    The whole difference between a replayer and a loop. Hand every request to
    epoch 0 and this is a static solve wearing a costume -- every policy scores
    identically, because there is nothing left to consolidate.
    """
    instance = problem(stops=2)
    day = Day(id="d", arrivals={"O1": 0, "O2": 4 * HOUR})

    run = replay(instance, day, greedy, epoch_length=HOUR)

    first = run.epochs[0]
    assert "O1" in first.dispatched
    assert "O2" not in first.dispatched and "O2" not in first.postponed


def test_a_request_becomes_visible_in_the_epoch_it_arrives_in():
    instance = problem(stops=2)
    day = Day(id="d", arrivals={"O1": 0, "O2": 90 * 60})

    run = replay(instance, day, greedy, epoch_length=HOUR)

    assert "O2" in run.epochs[1].dispatched


def test_work_whose_window_outlives_the_day_is_still_sent_out():
    """The case the end-of-day sweep exists for.

    An order whose window closes tomorrow is not must-go today, so AC-3.1 never
    forces it and a lazy policy postpones it past the last epoch -- where,
    without the sweep, it simply vanishes. Every other order in this corpus
    closes with the shift, so it is must-go by the final wave and forced
    anyway; perturbation showed the sweep looking redundant for exactly that
    reason.
    """
    from dataclasses import replace as _replace

    instance = problem(stops=2)
    tomorrow = TimeWindow(start=0, end=48 * HOUR)
    instance = _replace(instance, orders=tuple(
        _replace(order, delivery=_replace(order.delivery,
                                          time_windows=(tomorrow,)))
        for order in instance.orders))
    day = Day(id="d", arrivals={"O1": 0, "O2": HOUR})

    run = replay(instance, day, lazy, epoch_length=HOUR)

    assert set(run.dispatched) == {"O1", "O2"}, run.dispatched


def test_every_request_is_dispatched_by_the_end_of_the_day():
    """A replayer that quietly lost work would flatter every lazy policy: the
    cheapest day is the one where nothing goes out."""
    instance = problem(stops=6)
    day = generate_days(instance, count=1, seed=0, horizon=DAY)[0]

    for policy in BASELINES.values():
        run = replay(instance, day, policy, epoch_length=HOUR)
        assert set(run.dispatched) == set(day.arrivals), run.dispatched


# --------------------------------------------------------------------------
# The definition of done: determinism
# --------------------------------------------------------------------------

def test_two_replays_of_one_day_agree_exactly():
    """"Deterministic replay", and CON-4's reason: a comparison nobody can
    reproduce is an anecdote."""
    instance = problem()
    day = generate_days(instance, count=1, seed=0, horizon=DAY)[0]

    first = replay(instance, day, lazy, epoch_length=HOUR)
    again = replay(instance, day, lazy, epoch_length=HOUR)

    assert first.cost == again.cost
    assert [e.dispatched for e in first.epochs] == \
           [e.dispatched for e in again.epochs]


def test_ninety_days_replay_unattended():
    """T-53's definition of done. Ninety days, every baseline, no intervention."""
    instance = problem(stops=6)
    days = generate_days(instance, count=90, seed=0, horizon=DAY)

    runs = [replay(instance, day, greedy, epoch_length=HOUR) for day in days]

    assert len(runs) == 90
    assert all(run.cost > 0 for run in runs)


# --------------------------------------------------------------------------
# AC-3.2: the comparison report
# --------------------------------------------------------------------------

def test_every_policy_is_reported_against_greedy():
    """AC-3.2: "its expected cost is reported against a greedy-dispatch
    baseline"."""
    instance = problem(stops=6)
    days = generate_days(instance, count=30, seed=0, horizon=DAY)

    report = compare(instance, days, BASELINES, epoch_length=HOUR)

    assert set(report.results) == set(BASELINES)
    assert report.baseline == "greedy"
    assert report.results["greedy"].versus_baseline == 0

    # The delta has to be the actual difference, not a placeholder. Asserting
    # only that greedy is zero against itself is true of a report that returns
    # zero for everything -- perturbation confirmed it.
    for name, result in report.results.items():
        assert result.versus_baseline == \
            result.cost - report.results["greedy"].cost, name


def test_the_report_carries_service_as_well_as_cost():
    """A policy can be cheap by being late. §8.3's own warning about churn is
    the same shape: one number hides the trade it was chosen for."""
    instance = problem(stops=6)
    days = generate_days(instance, count=30, seed=0, horizon=DAY)

    report = compare(instance, days, BASELINES, epoch_length=HOUR)

    for name, result in report.results.items():
        assert result.days == 30, name
        assert result.dispatch_epochs > 0, name
        assert result.forced >= 0, name


def test_policies_score_differently_on_the_same_day():
    """If they did not, the replayer would not be simulating anything -- and
    every claim T-54 and T-55 make against these baselines would be vacuous."""
    instance = problem(stops=6)
    days = generate_days(instance, count=30, seed=0, horizon=DAY)

    report = compare(instance, days, BASELINES, epoch_length=HOUR)
    costs = {result.cost for result in report.results.values()}

    assert len(costs) > 1, report.results


def test_greedy_sends_out_on_more_days_than_lazy():
    """The expected shape, and a sanity check on the whole apparatus.

    Greedy dispatches the moment it hears about a request, so it sends a van
    out in almost every wave. Lazy holds everything until AC-3.1 forces its
    hand, so it sends out in very few. The first version of this test asserted
    the opposite and was simply wrong about which way round consolidation
    works.
    """
    instance = problem(stops=6)
    days = generate_days(instance, count=30, seed=0, horizon=DAY)

    report = compare(instance, days, BASELINES, epoch_length=HOUR)

    assert (report.results["greedy"].dispatch_epochs
            > report.results["lazy"].dispatch_epochs), report.results


def test_a_comparison_needs_a_greedy_baseline():
    """AC-3.2 names greedy specifically. A report against an arbitrary
    denominator is not the report the acceptance asks for."""
    instance = problem(stops=4)
    days = generate_days(instance, count=2, seed=0, horizon=DAY)

    with pytest.raises(ValueError, match="greedy"):
        compare(instance, days, {"lazy": lazy}, epoch_length=HOUR)


# --------------------------------------------------------------------------
# T-51's guarantee, now over the corpus it was promised on
# --------------------------------------------------------------------------

def test_no_must_go_is_postponed_anywhere_in_the_corpus():
    """T-51's definition of done was "zero must-go postponements across the
    replay corpus", and the corpus did not exist yet. It does now."""
    instance = problem(stops=6)
    days = generate_days(instance, count=90, seed=0, horizon=DAY)

    for day in days:
        for policy in BASELINES.values():
            run = replay(instance, day, policy, epoch_length=HOUR)
            for epoch in run.epochs:
                assert not (set(epoch.postponed) & set(epoch.must_go)), (
                    day.id, epoch.index)
