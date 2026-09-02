"""Recurring visits over a horizon — FR-23, T-73.

Seven operations asked for this and they agree on where the coupling lives.
`UC-043`: "The decision is which days to visit which assets; optimising each
day independently makes the cycle infeasible." `UC-024`: "The unit of
optimisation is the month; a locally optimal Tuesday leaves an infeasible
Friday."

So the tests here are about the day assignment and about what compliance
means, which `UC-129` states precisely: "the next due date is set by the last
visit, so today's plan determines next year's feasible plan." Four visits is
not four visits -- four visits in January and none after is four visits and a
year out of compliance, and a scheduler minimising travel would produce exactly
that, because clustering is cheaper.
"""

from __future__ import annotations

import pytest

from vrp.periodic import (
    Compliance,
    Recurrence,
    compliance,
    eligible_days,
    schedule,
)


def test_visits_are_spread_across_the_horizon_not_clustered_in_it():
    """Breaks: daily optimisation. Travel alone prefers four inspections in one
    week; the interval is what makes that the wrong answer."""
    quarterly = Recurrence("EXTINGUISHER", visits=4)

    due = schedule([quarterly], horizon=28)
    report = compliance(due, [quarterly])["EXTINGUISHER"]

    assert len(report.days) == 4
    # The measure has to be the worst gap *including the ends*, not the
    # evenness of the gaps between visits. Four visits on days 0-3 have
    # perfectly even internal gaps of one day and leave twenty-five days
    # uncovered, so an evenness check passes the very schedule this test
    # exists to reject -- which is how its first version passed with the
    # spreading perturbed out.
    assert report.worst_interval <= -(-28 // 4) + 1, (
        f"visits fell on {report.days}, worst gap {report.worst_interval} of a "
        "28-day horizon: four visits in a fortnight is four visits and a "
        "fortnight out of compliance")


def test_compliance_counts_the_gaps_at_both_ends_of_the_horizon():
    """`UC-129`'s failure, exactly. A commitment visited on the first two days
    of a month has a worst *internal* gap of one and is twenty-eight days out
    of compliance, and a measure that looked only between visits would call
    that perfect."""
    monthly = Recurrence("INSPECTION", visits=2, max_interval=20)
    packed = {day: (["INSPECTION"] if day in (0, 1) else []) for day in range(30)}

    report = compliance(packed, [monthly])["INSPECTION"]

    assert report.visits_met, "two visits were made"
    assert not report.interval_met, (
        f"worst gap {report.worst_interval} against an allowed "
        f"{report.allowed_interval}: the twenty-nine days after the second "
        "visit are the whole problem")
    assert not report.met, "a commitment is both counts, not either"


def test_a_permitted_day_pattern_is_respected():
    """`UC-111`: "the anniversary window plus customer availability" -- a hard
    set of days, with the search free inside it."""
    thursdays = frozenset({3, 10, 17, 24})
    recurrence = Recurrence("BOILER", visits=2, permitted_days=thursdays)

    due = schedule([recurrence], horizon=28)
    days = compliance(due, [recurrence])["BOILER"].days

    assert set(days) <= thursdays, f"{days} includes a day nobody is home"
    assert len(days) == 2


def test_a_commitment_its_permitted_days_cannot_keep_is_refused():
    """An impossible contract is not a planning shortfall. Reporting it as one
    would leave a dispatcher looking for a fleet that would fix it."""
    with pytest.raises(ValueError, match="permitted day"):
        schedule([Recurrence("WEEKLY", visits=5,
                             permitted_days=frozenset({0, 7}))], horizon=14)

    with pytest.raises(ValueError, match="between visits"):
        schedule([Recurrence("TIGHT", visits=2, max_interval=3,
                             permitted_days=frozenset({0, 1}))], horizon=30)


def test_compliance_is_reported_per_order():
    """FR-23's own words, and the reason it is a report rather than a flag: a
    horizon with one commitment missed and forty kept is not "non-compliant"."""
    recurrences = [Recurrence("A", visits=2), Recurrence("B", visits=4),
                   Recurrence("C", visits=1)]

    report = compliance(schedule(recurrences, horizon=20), recurrences)

    assert set(report) == {"A", "B", "C"}
    assert all(isinstance(row, Compliance) for row in report.values())
    assert all(row.met for row in report.values())
    assert [row.required for row in report.values()] == [2, 4, 1]


def test_a_commitment_nobody_scheduled_is_reported_as_missed_not_omitted():
    """A silent omission is the failure mode this whole module exists to make
    impossible: an order that simply is not in the schedule looks identical to
    one nobody committed to."""
    never = Recurrence("FORGOTTEN", visits=3, max_interval=10)

    report = compliance({day: [] for day in range(30)}, [never])["FORGOTTEN"]

    assert report.days == ()
    assert not report.visits_met and not report.interval_met
    assert report.worst_interval == 30, (
        "a commitment never kept is the whole horizon out of compliance, not "
        "zero days")


def test_eligible_days_treats_an_empty_pattern_as_unrestricted():
    """Empty means "any day", not "no day" -- the same reading `access_classes`
    takes, and for the same reason: the inverse would make an ordinary
    commitment unschedulable."""
    assert eligible_days(Recurrence("ANY", visits=1), horizon=5) == [0, 1, 2, 3, 4]
    assert eligible_days(Recurrence("SOME", visits=1,
                                    permitted_days=frozenset({1, 3})),
                         horizon=5) == [1, 3]
