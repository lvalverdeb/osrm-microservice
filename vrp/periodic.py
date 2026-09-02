"""Recurring visits over a horizon — FR-23, §12.2, T-73.

FR-23: "Support a **multi-period planning horizon**: recurring visits planned
across several periods as one problem rather than as independent days, with
per-order visit frequency, permitted-day patterns, and compliance measured
against the interval rather than the day."

Seven operations asked for it and they agree on where the coupling lives.
`UC-043`: "The decision is which days to visit which assets; optimising each
day independently makes the cycle infeasible." `UC-024`: "The unit of
optimisation is the month; a locally optimal Tuesday leaves an infeasible
Friday." So this module decides the days, and each day is then an ordinary
single-day problem for the ordinary solver. Choosing the days *is* planning the
horizon as one problem; solving all of them jointly would be a different and
much larger claim, and none of the citing entries makes it.

**A recurrence is not an order.** An order is one visit at one place on one
day, which is what the domain model, the evaluator and the verifier all take it
to be. A commitment to inspect a fire extinguisher four times a year is a
statement about a *set* of orders that do not exist yet. Putting a frequency
field on `Order` would have made every single-day plan carry a field meaning
nothing, so the recurrence sits above the model and produces orders rather than
being one.

**Compliance is measured against the interval, not the calendar.** `UC-129`:
"the next due date is set by the last visit, so today's plan determines next
year's feasible plan." Four visits in a year is not four visits: four visits in
January and none after is four visits and a year out of compliance. So the
measure is the longest gap between consecutive visits, including the gaps at
each end of the horizon, against what the commitment allows.

Placement: **Python**, per criterion 2. This orchestrates the domain model and
hands days to a solver; it is nowhere near a request path.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

MAX_INTERVAL_UNBOUNDED = 10 ** 9


@dataclass(frozen=True)
class Recurrence:
    """A standing commitment to visit something, repeatedly. FR-23.

    Attributes:
        order_id: the work that recurs. One id, many visits.
        visits: how many times over the horizon. `UC-152`'s "call frequency by
            account tier" and `UC-129`'s statutory interval both land here.
        max_interval: the longest gap the commitment tolerates, in days.
            Defaults to unbounded, which makes a frequency a frequency and
            nothing more -- four visits whenever suits.
        permitted_days: day indices the visit may fall on; empty means any.
            `UC-111`'s "anniversary window plus customer availability" is this:
            a hard set of days with the search free inside it.
    """

    order_id: str
    visits: int
    max_interval: int = MAX_INTERVAL_UNBOUNDED
    permitted_days: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.visits < 1:
            raise ValueError(f"{self.order_id}: a recurrence needs a visit")
        if self.max_interval < 1:
            raise ValueError(f"{self.order_id}: an interval needs a day")


@dataclass(frozen=True)
class Compliance:
    """Whether a commitment was kept, and by how much it was missed."""

    order_id: str
    days: tuple[int, ...]
    required: int
    worst_interval: int
    allowed_interval: int

    @property
    def visits_met(self) -> bool:
        return len(self.days) >= self.required

    @property
    def interval_met(self) -> bool:
        return self.worst_interval <= self.allowed_interval

    @property
    def met(self) -> bool:
        return self.visits_met and self.interval_met


def eligible_days(recurrence: Recurrence, horizon: int) -> list[int]:
    """The days this commitment may fall on, within the horizon."""
    days = range(horizon)
    if not recurrence.permitted_days:
        return list(days)
    return [day for day in days if day in recurrence.permitted_days]


def schedule(recurrences, horizon: int) -> dict[int, list[str]]:
    """Which days to visit which assets. FR-23, `UC-043`.

    Args:
        recurrences: the standing commitments.
        horizon: how many days are being planned.

    Returns:
        Day index to the order ids due that day, days in order.

    Raises:
        ValueError: if a commitment cannot be met at all -- more visits than it
            has permitted days, or an interval its permitted days cannot span.
            Refused here rather than reported as a shortfall later, because an
            impossible commitment is a contract nobody should have signed and a
            plan is not the place to discover it.

    Visits are spread evenly across the eligible days rather than packed. That
    is the whole point of the interval: `UC-129`'s four inspections in January
    are four inspections and eleven months out of compliance, and a scheduler
    optimising travel alone would produce exactly that because clustering is
    cheaper.
    """
    due: dict[int, list[str]] = {day: [] for day in range(horizon)}
    for recurrence in sorted(recurrences, key=lambda r: r.order_id):
        days = eligible_days(recurrence, horizon)
        if len(days) < recurrence.visits:
            raise ValueError(
                f"{recurrence.order_id} asks for {recurrence.visits} visits on "
                f"{len(days)} permitted day(s) in a {horizon}-day horizon")
        chosen = _spread(days, recurrence.visits)
        worst = _worst_interval(chosen, horizon)
        if worst > recurrence.max_interval:
            raise ValueError(
                f"{recurrence.order_id} allows {recurrence.max_interval} days "
                f"between visits; its permitted days leave a gap of {worst}")
        for day in chosen:
            due[day].append(recurrence.order_id)
    return due


def _spread(days: list[int], visits: int) -> tuple[int, ...]:
    """`visits` of `days`, as evenly separated as the permitted set allows.

    Deterministic (CON-4) and biased to the front only where the arithmetic
    forces it: with three visits over ten days the gaps are 3-3-4, not 3-3-3
    and a long tail.
    """
    if visits >= len(days):
        return tuple(days)
    step = len(days) / visits
    return tuple(days[min(len(days) - 1, int(index * step))]
                 for index in range(visits))


def _worst_interval(days: tuple[int, ...], horizon: int) -> int:
    """The longest gap between consecutive visits, ends included.

    The ends matter and are easy to leave out. A commitment visited on days 0
    and 1 of a thirty-day month has a worst *internal* gap of one day and is
    twenty-nine days out of compliance, which is the failure `UC-129` names.
    """
    if not days:
        return horizon
    gaps = [days[0] + 1, horizon - days[-1]]
    gaps.extend(later - earlier
                for earlier, later in pairwise(days))
    return max(gaps)


def compliance(due: dict[int, list[str]], recurrences) -> dict[str, Compliance]:
    """Per commitment: when it was visited, and whether that kept it. FR-23.

    Measured from the schedule rather than from the plans, deliberately. A day
    the scheduler assigned and the solver could not serve is a different
    failure with a different owner -- `preflight` and the unassigned list
    report that one -- and folding the two together would leave nobody able to
    tell an over-committed contract from an under-sized fleet.
    """
    horizon = len(due)
    visited: dict[str, list[int]] = {}
    for day in sorted(due):
        for order_id in due[day]:
            visited.setdefault(order_id, []).append(day)
    return {
        recurrence.order_id: Compliance(
            order_id=recurrence.order_id,
            days=tuple(visited.get(recurrence.order_id, ())),
            required=recurrence.visits,
            worst_interval=_worst_interval(
                tuple(visited.get(recurrence.order_id, ())), horizon),
            allowed_interval=recurrence.max_interval)
        for recurrence in sorted(recurrences, key=lambda r: r.order_id)}
