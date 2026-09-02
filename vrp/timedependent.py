"""Travel that depends on when you leave — FR-14, §6.3, MTX-9, T-40.

§6.3 states the requirement and forbids the obvious implementation in the same
breath: "Per-arc (or per-zone) piecewise-constant **speed** profiles over time
buckets... Piecewise-constant *travel time* per bucket violates FIFO and MUST
NOT be used; the Ichoua–Gendreau–Potvin construction (speed changes when a
bucket boundary is crossed mid-arc) is the required formulation."

That distinction is the whole module. Bucketing *travel time* is one line
shorter and lets a van that leaves at 08:59 arrive after one that leaves at
09:01, because the later departure is charged a different flat rate for the
whole arc. Bucketing *speed* and changing rate mid-arc cannot: leaving later
means being strictly behind at every instant, so arriving earlier is
arithmetically impossible. §6.3 calls that the FIFO or no-passing property and
`test_leaving_later_never_arrives_earlier` is the property test for it.

**What this is not.** It is the evaluator, not the data. Profiles here are
whatever a caller supplies, and nothing claims they resemble a real afternoon:
`T-63` fits them from executed routes against the engine's free-flow
assumptions, which is `§12.2`'s construction and needs telematics this stack
does not yet have. Separating the two is what let the evaluator be built at all: `T-40`'s original
blocker said there was "nothing to fit profiles against" and concluded nothing
could be done, when the FIFO property and the filter's false-negative rate --
its whole definition of done -- are properties of the construction rather than
of anybody's traffic.

**Multipliers, not speeds, and integers throughout.** §12.2 fits "per-arc-class,
per-bucket speed multipliers... against the routing engine's free-flow
assumptions", so a profile scales a free-flow duration the matrix already
carries rather than restating a speed the road network already knows. Parts per
thousand, matching `Vehicle.service_factor_ppt`, and the arithmetic is exact
integer work: CON-4 wants two machines to agree, and a float here is how they
stop agreeing on when a driver left.

Placement: **Python**, alongside the evaluator it will eventually serve. If
time-dependent evaluation becomes the inner loop of local search rather than a
component under test, it is a tight numeric routine with the same argument for
Rust that `vrp.localsearch` has.
"""

from __future__ import annotations

from dataclasses import dataclass

PPT = 1_000
DAY_SECONDS = 24 * 3_600


@dataclass(frozen=True)
class SpeedProfile:
    """Piecewise-constant speed over equal buckets, repeating each day.

    Attributes:
        bucket_seconds: how long each bucket lasts. §6.3 suggests 15–60
            minutes; nothing here depends on that.
        multipliers_ppt: one per bucket, in parts per thousand of free-flow
            speed. 1000 is free flow, 500 is half of it, and the buckets wrap,
            so a profile describes a day and is asked about any instant.
    """

    bucket_seconds: int
    multipliers_ppt: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.bucket_seconds <= 0:
            raise ValueError("a bucket must have a positive length")
        if not self.multipliers_ppt:
            raise ValueError("a profile needs at least one bucket")
        if any(m <= 0 for m in self.multipliers_ppt):
            # A zero multiplier is a road nobody ever leaves, which is an
            # unreachable arc (MTX-5) wearing a speed profile. Refused here so
            # `travel` can promise to terminate.
            raise ValueError("a speed multiplier must be positive; a zero one "
                             "is an unreachable arc, not slow traffic")

    @property
    def span(self) -> int:
        return self.bucket_seconds * len(self.multipliers_ppt)

    def multiplier_at(self, instant: int) -> int:
        """The multiplier in force at `instant`. Wraps, so any instant works."""
        return self.multipliers_ppt[
            (instant % self.span) // self.bucket_seconds]

    @property
    def fastest_ppt(self) -> int:
        return max(self.multipliers_ppt)


# Arc classes by free-flow duration, in the order they are tested. §6.3 asks
# for profiles "per arc (or per zone)" and §12.2 fits multipliers per arc
# class; duration bands are the classification the matrix already supports,
# and a dispatcher can check which band an arc is in by reading one number.
# Road category would be better and is not in the domain: deriving it from
# coordinates would put an unauditable label in an auditable pipeline.
#
# They live here rather than with the calibration because the *model* has to
# classify an arc to pick its profile, and `vrp.model` imports this module.
ARC_CLASSES = ((300, "local"), (1_200, "arterial"))
TRUNK = "trunk"


def arc_class_of(free_flow_seconds: int) -> str:
    """Which class an arc belongs to, by what the engine thinks it costs."""
    for ceiling, name in ARC_CLASSES:
        if free_flow_seconds <= ceiling:
            return name
    return TRUNK


def arc_class_names() -> tuple[str, ...]:
    """Every class an arc can be, longest last."""
    return tuple(name for _, name in ARC_CLASSES) + (TRUNK,)


@dataclass(frozen=True)
class ArcKey:
    """§12.2's grouping key: a class of road at a bucket of the day."""

    arc_class: str
    bucket: int


def travel(free_flow_seconds: int, depart: int, profile: SpeedProfile) -> int:
    """How long an arc takes, leaving at `depart`. The IGP construction.

    Walks bucket boundaries, changing speed at each one, and charges the
    remaining distance at the rate in force. Distance is tracked in
    milli-free-flow-seconds -- the free-flow duration scaled by `PPT` -- so a
    second in a bucket of multiplier `m` consumes exactly `m` of them and the
    whole computation stays in integers.

    Args:
        free_flow_seconds: what the arc costs with no congestion, from the
            pinned matrix.
        depart: when the vehicle leaves.
        profile: the speeds along the way.

    Returns:
        The duration, rounded up. Rounding up rather than to nearest is
        deliberate: an arrival the plan promises and the road does not deliver
        is a lateness the dispatcher explains, and half a second of optimism
        per arc accumulates in the direction nobody wants.
    """
    if free_flow_seconds < 0:
        raise ValueError("a free-flow duration cannot be negative")
    if free_flow_seconds == 0:
        return 0

    remaining = free_flow_seconds * PPT
    clock = depart
    elapsed = 0
    while remaining > 0:
        multiplier = profile.multiplier_at(clock)
        # Whole seconds to the next boundary, so speed changes land exactly on
        # it rather than a rounding error either side.
        to_boundary = profile.bucket_seconds - (clock % profile.bucket_seconds)
        # Ceiling division: the last partial second still has to be driven.
        needed = -(-remaining // multiplier)
        if needed <= to_boundary:
            return elapsed + needed
        remaining -= to_boundary * multiplier
        elapsed += to_boundary
        clock += to_boundary
    return elapsed


def arrival(free_flow_seconds: int, depart: int, profile: SpeedProfile) -> int:
    return depart + travel(free_flow_seconds, depart, profile)


def fastest_possible(free_flow_seconds: int, profile: SpeedProfile) -> int:
    """A lower bound on `travel`, at any departure whatever. §6.3's filter.

    The arc driven end to end at the best speed the profile ever offers. No
    real departure can beat it, which is what makes it safe to prune with: if
    even this arrives too late, every departure does.

    Admissible by construction and checked by
    `test_the_bound_is_never_beaten_by_a_real_departure`, because a bound that
    is occasionally wrong is worse than none -- it prunes a move that was fine
    and nothing downstream ever learns the plan was avoidable.
    """
    if free_flow_seconds <= 0:
        return 0
    return -(-free_flow_seconds * PPT // profile.fastest_ppt)


@dataclass(frozen=True)
class FilterReport:
    """How much work the cheap bound saved, and how much it did not.

    `T-40`'s definition of done asks for "false-negative rate of the filter
    reported", which the specification does not define, so this does: a
    *negative* is the filter declining to prune. It is *false* when the exact
    evaluation then rejects the move anyway -- work the bound could not save.
    The filter never prunes a feasible move, which is a correctness property
    and is tested separately rather than counted here.
    """

    considered: int
    pruned: int
    passed_then_rejected: int

    @property
    def false_negative_rate_ppt(self) -> int:
        """Share of infeasible moves the bound failed to catch, in ppt."""
        missed = self.passed_then_rejected
        infeasible = self.pruned + missed
        return 0 if infeasible == 0 else missed * PPT // infeasible

    @property
    def pruned_share_ppt(self) -> int:
        return 0 if not self.considered else self.pruned * PPT // self.considered


def filter_moves(moves, profile: SpeedProfile) -> FilterReport:
    """Apply the bound to `(free_flow, depart, deadline)` triples and report.

    A move is feasible when the vehicle arrives by its deadline. The bound
    prunes what cannot make it at any speed; everything else is evaluated
    exactly. Nothing here changes a plan -- it measures a filter so §6.3's
    claim that filtering is worth doing can be checked rather than believed.
    """
    considered = pruned = missed = 0
    for free_flow, depart, deadline in moves:
        considered += 1
        if depart + fastest_possible(free_flow, profile) > deadline:
            pruned += 1
            continue
        if arrival(free_flow, depart, profile) > deadline:
            missed += 1
    return FilterReport(considered=considered, pruned=pruned,
                        passed_then_rejected=missed)
