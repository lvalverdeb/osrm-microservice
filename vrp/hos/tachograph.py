"""Reading a driver's recorded day into `DriverState` — §6.4, AC-5.2, T-26.

§6.4 names the authority: "Where tachograph or ELD data is available, it is the
authoritative source for `initial_state`." E-25 built the mechanism that
consumes that state; this is what fills it, so the carry-over stops being a
number somebody works out by hand and becomes what the driver's device recorded.

**The hard part is not arithmetic, it is scope.** A driver who drove nine hours
yesterday, slept eleven, and has driven two this morning has consumed two hours,
not eleven. Everything before a qualifying daily rest belongs to a finished duty
and must not be charged against today's envelope -- and a reader that summed the
whole feed would refuse to plan a day that is entirely legal.

What counts as "qualifying" is per rule set and not a detail: EU-561 wants 11
hours of daily rest, US-HOS wants 10. A ten-hour break resets the day for an
American driver and does not for a European one, so the same feed produces two
different states depending on which law the driver is under. `read_duty` takes
the rule set for exactly that reason.

Records are folded through the rule set's own `advance()`, never counted here.
Two implementations of "what does a DRIVE record do to a driver" would be two
chances to disagree, and the one in `vrp.hos.rules` is the one the scheduler and
the verifier already trust.

Bad input is refused rather than repaired. Real ELD feeds contain gaps,
overlaps, out-of-order rows and vendor-specific statuses, and every plausible
repair -- sorting, clipping, treating an unknown status as off-duty -- invents
hours the driver did not work, always in the direction that flatters the plan.

Placement: Python. Regulatory input parsing, beside the rules engine that
interprets it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from vrp.hos.rules import Activity, DriverState, HoursOfServiceRules, rules_for

# §6.4's daily rest, per shipped rule set. A rest at least this long ends the
# previous duty; anything shorter is a break within it.
DAILY_REST: dict[str, int] = {
    "EU-561": 11 * 3600,      # "Daily rest of at least 11 h"
    "US-HOS": 10 * 3600,      # "after 10 consecutive hours off duty"
}


@dataclass(frozen=True)
class DutyRecord:
    """One activity from a tachograph or ELD, in whole seconds.

    `activity` uses the rules engine's vocabulary (DRIVE, WORK, BREAK, REST,
    WAIT) rather than any device's. Mapping a vendor's status codes onto these
    is the integration's job and is deliberately not guessed at here.
    """

    activity: str
    start: int
    end: int

    @property
    def seconds(self) -> int:
        return self.end - self.start


def _validate(records: tuple[DutyRecord, ...]) -> None:
    """Refuse a feed that cannot be read honestly.

    Each repair one might apply here invents hours: sorting assumes the device
    is right about durations but wrong about order, clipping an overlap picks
    one activity over another, and an unknown status defaults to whichever
    reading is most convenient. All three err towards a driver with more hours
    left than they have.
    """
    known = {member.value for member in Activity}
    previous: DutyRecord | None = None
    for entry in records:
        if entry.activity not in known:
            raise ValueError(
                f"unknown activity {entry.activity!r}; the rules engine knows "
                f"{', '.join(sorted(known))}")
        if entry.end < entry.start:
            raise ValueError(
                f"record ends before it starts: {entry.start} to {entry.end}")
        if previous is not None:
            if entry.start < previous.start:
                raise ValueError(
                    f"records are out of order: {entry.start} follows "
                    f"{previous.start}")
            if entry.start < previous.end:
                raise ValueError(
                    f"records overlap: {previous.activity} runs to "
                    f"{previous.end}, {entry.activity} starts at {entry.start}")
        previous = entry


def daily_rest_for(rules: HoursOfServiceRules) -> int:
    """How long a rest must be to end a duty under this rule set."""
    try:
        return DAILY_REST[rules.name]
    except KeyError:
        raise ValueError(
            f"no daily-rest length known for {rules.name!r}; add it to "
            f"DAILY_REST rather than assuming one") from None


def read_duty(records: tuple[DutyRecord, ...] | list[DutyRecord],
              rules: HoursOfServiceRules) -> DriverState:
    """Fold a recorded day into the state the planner should start from.

    Args:
        records: activities in chronological order, non-overlapping.
        rules: the rule set the driver is subject to. Decides both how each
            activity is accounted and how long a rest must be to end a duty.

    Returns:
        The driver's consumed hours for the *current* duty, with the weekly
        driving total carried across the daily resets.

    Raises:
        ValueError: the feed is out of order, overlapping, contains an unknown
            activity, or a record ends before it starts.
    """
    records = tuple(records)
    _validate(records)
    threshold = daily_rest_for(rules)

    state = DriverState()
    for entry in records:
        if entry.activity in ("REST", "BREAK") and entry.seconds >= threshold:
            # A qualifying rest ends the duty. The week does not reset with it:
            # EU-561's 56-hour weekly limit spans days by definition, and
            # dropping the total here would lose the only record of it.
            state = DriverState(week_drive_used=state.week_drive_used)
            continue
        state = rules.advance(state, Activity(entry.activity), entry.seconds)
    return state


def resume_from(vehicle, records: tuple[DutyRecord, ...] | list[DutyRecord]):
    """A copy of `vehicle` carrying the driver's recorded consumption.

    Args:
        vehicle: must declare `hos_rules`. Carry-over without a rule set is
            meaningless -- "six hours consumed" is a constraint only relative
            to a limit -- so it is refused rather than stored and ignored.
        records: the driver's day, as `read_duty` takes it.

    Returns:
        The same vehicle with `initial_state` set, ready to plan against.
    """
    if not vehicle.hos_rules:
        raise ValueError(
            f"vehicle {vehicle.id} declares no hos_rules, so recorded hours "
            f"cannot be interpreted; set one before resuming a duty")
    rules = rules_for(vehicle.hos_rules)
    return replace(vehicle, initial_state=read_duty(records, rules))
