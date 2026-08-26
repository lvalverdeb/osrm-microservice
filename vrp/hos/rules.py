"""Hours-of-service rules engine — SDD §6.4, FR-15, FR-16, T-25.

Working-time law is a hard legal constraint with criminal and licensing
consequences, so this module is deliberately literal: each rule set states the
regulation's numbers as named constants and nothing infers them.

The interface is §6.4's, unchanged:

    init_state(carry_over) -> DriverState
    can_drive(state, seconds) -> bool
    advance(state, activity, seconds) -> DriverState
    required_break(state) -> Break | None
    remaining_drive(state) -> int

`DriverState` is immutable and `advance` returns a new one. That is what lets a
scheduler explore a route without unwinding mutations, and it is why the
accumulators are plain integers rather than a class with methods.

**What is modelled here is the single duty.** EU's weekly (56 h) and fortnightly
(90 h) limits, the twice-weekly extension to 10 h, and the reducible daily rest
all need a horizon longer than one route, which this planner does not have. They
are carried as `week_drive_used` so a day can be refused against an envelope
already partly consumed, but they are not *planned* across days -- that needs
`T-26`'s tachograph carry-over and a multi-day model. Refusing to plan is the
safe direction: the failure mode of pretending otherwise is a legal one.

Placement: Python. This is regulatory logic that changes when regulations do,
it is not on the request path, and it belongs beside the constraint model rather
than in the transport layer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class Activity(Enum):
    """§6.4's activity vocabulary."""

    DRIVE = "DRIVE"
    WORK = "WORK"      # loading, paperwork -- duty but not driving
    BREAK = "BREAK"
    REST = "REST"
    WAIT = "WAIT"      # waiting for a window to open; still on duty


class Placement(Enum):
    """Where a break may be taken. §6.4, "Placement"."""

    ANYWHERE_ON_ARC = "ANYWHERE_ON_ARC"
    AT_CUSTOMER = "AT_CUSTOMER"
    AT_FACILITY = "AT_FACILITY"


@dataclass(frozen=True)
class DriverState:
    """Consumption so far. All values whole seconds.

    `duty_used` is wall-clock since coming on duty, which is why waiting counts
    towards it and driving hours never restore it.
    """

    drive_used: int = 0
    duty_used: int = 0
    since_last_break: int = 0
    week_drive_used: int = 0


@dataclass(frozen=True)
class Break:
    """What must happen next, and under which article."""

    duration: int
    rule_ref: str
    placement: Placement = Placement.ANYWHERE_ON_ARC


@dataclass(frozen=True)
class HoursOfServiceRules:
    """One regulatory rule set.

    A frozen dataclass rather than a subclass hierarchy: every shipped rule set
    so far differs only in its numbers and its citation, so an abstract base
    class would add a layer without adding a decision. A rule set whose *shape*
    differs -- split breaks, for instance -- should override the methods rather
    than squeeze into these fields.
    """

    name: str
    max_drive: int                  # driving in one duty
    max_duty: int                   # wall-clock window from coming on duty
    drive_before_break: int         # accumulated driving forcing a break
    break_duration: int
    break_rule_ref: str

    def init_state(self, carry_over: DriverState | None = None) -> DriverState:
        """Start a duty, honouring hours already consumed.

        §6.4 makes this mandatory input: planning a fresh day for a driver who
        already drove six hours is a compliance incident, not a modelling
        nicety. The default is a rested driver because that is the only safe
        thing to assume in the absence of tachograph data -- and `T-26` is what
        replaces the assumption with evidence.
        """
        return carry_over or DriverState()

    def advance(self, state: DriverState, activity: Activity,
                seconds: int) -> DriverState:
        """Consume time. Returns a new state; never mutates."""
        if seconds < 0:
            raise ValueError("cannot advance by negative seconds")
        if activity in (Activity.BREAK, Activity.REST):
            # A break restores the driving *block*, not the daily total. Duty
            # keeps running: the 14h US window does not pause for a break.
            return replace(state, duty_used=state.duty_used + seconds,
                           since_last_break=0)
        if activity is Activity.DRIVE:
            return replace(state,
                           drive_used=state.drive_used + seconds,
                           duty_used=state.duty_used + seconds,
                           since_last_break=state.since_last_break + seconds,
                           week_drive_used=state.week_drive_used + seconds)
        # WORK and WAIT consume the duty window only.
        return replace(state, duty_used=state.duty_used + seconds)

    def remaining_drive(self, state: DriverState) -> int:
        """Driving seconds left in the *duty*, once a break is taken as needed.

        Deliberately not capped by the break interval. The two are different
        questions -- "how much more can this driver do today" against "how much
        before they must stop for 45 minutes" -- and answering both with one
        number was the first shape of this class. It made a rested driver look
        like they had 4.5 hours in them rather than 9, which reads as a fleet
        a third smaller than it is.
        """
        return max(0, min(self.max_drive - state.drive_used,
                          self.max_duty - state.duty_used))

    def drive_until_break(self, state: DriverState) -> int:
        """Driving seconds available *right now*, before a break falls due."""
        return min(self.remaining_drive(state),
                   max(0, self.drive_before_break - state.since_last_break))

    def can_drive(self, state: DriverState, seconds: int) -> bool:
        """May the driver drive this long without stopping first?"""
        return seconds <= self.drive_until_break(state)

    def required_break(self, state: DriverState) -> Break | None:
        """What must happen before driving may continue, and why.

        Returns `None` when driving may continue, and `None` also when a break
        would not help -- a driver out of daily hours needs a rest, not a break,
        and saying "take 45 minutes" there would be wrong advice rather than
        merely unhelpful. `remaining_drive` is what distinguishes the two.
        """
        if state.since_last_break < self.drive_before_break:
            return None
        if state.drive_used >= self.max_drive or state.duty_used >= self.max_duty:
            return None
        return Break(duration=self.break_duration, rule_ref=self.break_rule_ref)


HOUR = 3600

# Daily driving 9h; 45-minute break after at most 4.5h accumulated driving.
# The extension to 10h twice weekly is not planned -- see the module docstring.
EU_561 = HoursOfServiceRules(
    name="EU-561",
    max_drive=9 * HOUR,
    # 561/2006 caps driving and rest rather than a duty window as such; the
    # working-time ceiling comes from Directive 2002/15/EC. 13h is the span left
    # by the 11h daily rest, which is the binding wall-clock limit in practice.
    max_duty=13 * HOUR,
    drive_before_break=int(4.5 * HOUR),
    break_duration=45 * 60,
    break_rule_ref="EC-561/2006 Art.7",
)

# 11h driving inside a 14h duty window; 30-minute interruption after 8h driving.
US_HOS = HoursOfServiceRules(
    name="US-HOS",
    max_drive=11 * HOUR,
    max_duty=14 * HOUR,
    drive_before_break=8 * HOUR,
    break_duration=30 * 60,
    break_rule_ref="49 CFR 395.3(a)(3)(ii)",
)

_SHIPPED = {rules.name: rules for rules in (EU_561, US_HOS)}


def rules_for(name: str) -> HoursOfServiceRules:
    """Resolve a shipped rule set by name. FR-15's pluggability, concretely."""
    try:
        return _SHIPPED[name]
    except KeyError:
        raise ValueError(
            f"unknown hours-of-service rule set {name!r}; "
            f"shipped: {', '.join(sorted(_SHIPPED))}") from None
