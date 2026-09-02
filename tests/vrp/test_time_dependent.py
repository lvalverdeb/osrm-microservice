"""Travel that depends on when you leave — FR-14, §6.3, T-40.

§6.3 forbids an implementation as firmly as it requires one: "Piecewise-constant
*travel time* per bucket violates FIFO and MUST NOT be used; the
Ichoua–Gendreau–Potvin construction (speed changes when a bucket boundary is
crossed mid-arc) is the required formulation."

`test_the_forbidden_formulation_really_does_break_fifo` builds the banned
version and shows a van leaving later arriving earlier. It is the only test
here that asserts something about code this module does not contain, and it
earns its place: without it, the FIFO property test passes trivially and nobody
can tell whether the construction is doing the work or the instance is too easy
to distinguish the two.

Profiles are synthetic and nothing pretends otherwise. `T-40` is the evaluator;
fitting profiles that resemble a real afternoon is `T-63`, which needs
telematics this stack does not have. The properties tested here -- FIFO, and an
admissible bound -- are properties of the arithmetic, and hold for any profile
anybody ever fits.
"""

from __future__ import annotations

import random

import pytest

from vrp.timedependent import (
    PPT,
    FilterReport,
    SpeedProfile,
    arrival,
    fastest_possible,
    filter_moves,
    travel,
)

HOUR = 3600


def peak_profile() -> SpeedProfile:
    """Free flow overnight, half speed through a three-hour morning peak."""
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if 7 <= hour <= 9 else 1000
                              for hour in range(24)))


def random_profile(rng: random.Random) -> SpeedProfile:
    buckets = rng.choice((4, 12, 24, 96))
    return SpeedProfile(
        bucket_seconds=DAY // buckets,
        multipliers_ppt=tuple(rng.randint(200, 2000) for _ in range(buckets)))


DAY = 24 * HOUR


# --------------------------------------------------------------------------
# The property the whole formulation exists for
# --------------------------------------------------------------------------

def test_leaving_later_never_arrives_earlier():
    """§6.3's FIFO / no-passing property, over randomised profiles.

    `T-40`'s definition of done, and the reason the construction is specified
    rather than left to the implementer: a plan that lets a later departure
    overtake an earlier one is not merely inaccurate, it is one a driver can
    disprove by watching the van in front.
    """
    rng = random.Random(40)
    for _ in range(2_000):
        profile = random_profile(rng)
        free_flow = rng.randint(1, 4 * HOUR)
        earlier = rng.randrange(0, DAY)
        later = earlier + rng.randint(1, 3 * HOUR)

        assert arrival(free_flow, earlier, profile) <= \
               arrival(free_flow, later, profile), (
            f"free_flow={free_flow} earlier={earlier} later={later} "
            f"buckets={profile.multipliers_ppt}")


def test_the_forbidden_formulation_really_does_break_fifo():
    """The banned implementation, built here so the ban has evidence.

    Charging the whole arc at the multiplier in force when the van left is one
    line shorter, and it overtakes at the *trailing* edge of a peak: the van
    leaving at 09:59 is charged the peak rate for a two-hour arc that spends
    one minute of it in the peak, while the van leaving at 10:01 is charged
    free flow and passes it. §6.3 calls that "piecewise-constant travel time
    per bucket" and forbids it.
    """
    profile = peak_profile()
    free_flow = 2 * HOUR

    def banned(depart: int) -> int:
        """One flat rate for the whole arc, chosen at departure."""
        return depart + free_flow * PPT // profile.multiplier_at(depart)

    earlier, later = 10 * HOUR - 60, 10 * HOUR + 60   # 09:59 and 10:01

    assert banned(earlier) > banned(later), (
        "this test exists to show the forbidden formulation overtaking; if it "
        "no longer does, the profile is too flat to tell the two apart")
    assert arrival(free_flow, earlier, profile) <= \
           arrival(free_flow, later, profile), (
        "and the required one must not, on the very same instance")


def test_speed_changes_at_the_boundary_not_at_departure():
    """The IGP construction, in one arc.

    An hour of free-flow driving that starts at 06:30 spends half an hour
    before the peak and finishes inside it, so it takes longer than an hour and
    less than two. Charging either flat rate would give one of the endpoints.
    """
    profile = peak_profile()
    one_hour_arc = HOUR

    crossing = travel(one_hour_arc, 6 * HOUR + 1800, profile)

    assert HOUR < crossing < 2 * HOUR, crossing
    assert crossing == 1800 + 3600, (
        "half an hour at free flow covers half the arc; the remaining half "
        "takes an hour at half speed")


# --------------------------------------------------------------------------
# The bound §6.3 filters with
# --------------------------------------------------------------------------

def test_the_bound_is_never_beaten_by_a_real_departure():
    """Admissibility, which is a correctness property rather than a tuning one.

    A bound that is occasionally optimistic prunes a move that was feasible,
    and nothing downstream ever learns the plan was avoidable.
    """
    rng = random.Random(63)
    for _ in range(2_000):
        profile = random_profile(rng)
        free_flow = rng.randint(0, 3 * HOUR)
        depart = rng.randrange(0, DAY)

        assert fastest_possible(free_flow, profile) <= \
               travel(free_flow, depart, profile)


def test_the_filter_never_prunes_a_move_that_would_have_worked():
    """The same property, stated the way the search would use it."""
    rng = random.Random(7)
    profile = peak_profile()
    for _ in range(1_000):
        free_flow = rng.randint(60, 3 * HOUR)
        depart = rng.randrange(0, DAY)
        deadline = depart + rng.randint(60, 4 * HOUR)

        if depart + fastest_possible(free_flow, profile) > deadline:
            assert arrival(free_flow, depart, profile) > deadline, (
                "pruned a move the exact evaluation would have accepted")


def test_the_filter_reports_what_it_did_not_catch():
    """`T-40`'s other definition of done. The specification does not define
    "false-negative rate", so the module does, and this pins that reading: a
    negative is declining to prune, and it is false when the exact evaluation
    rejects the move anyway."""
    rng = random.Random(11)
    profile = peak_profile()
    moves = [(rng.randint(60, 3 * HOUR), rng.randrange(0, DAY), 0)
             for _ in range(500)]
    moves = [(ff, depart, depart + rng.randint(60, 3 * HOUR))
             for ff, depart, _ in moves]

    report = filter_moves(moves, profile)

    assert isinstance(report, FilterReport)
    assert report.considered == len(moves)
    assert report.pruned > 0, "a peak profile and tight deadlines must prune"
    assert report.passed_then_rejected > 0, (
        "and a lower bound cannot catch everything, which is the number worth "
        "reporting rather than the one worth hiding")
    assert 0 < report.false_negative_rate_ppt < PPT
    print(f"\n   filter: pruned {report.pruned_share_ppt / 10:.1f}% of "
          f"{report.considered} moves; false negatives "
          f"{report.false_negative_rate_ppt / 10:.1f}% of the infeasible ones")


def test_a_flat_profile_is_the_matrix_it_started_from():
    """Free flow everywhere must cost exactly what the matrix said, or the
    construction has introduced a discrepancy where there was none."""
    flat = SpeedProfile(bucket_seconds=900, multipliers_ppt=(PPT,) * 96)

    for depart in (0, 1, 899, 900, 12 * HOUR, DAY - 1):
        assert travel(1234, depart, flat) == 1234


# --------------------------------------------------------------------------
# What the model refuses
# --------------------------------------------------------------------------

def test_a_stopped_road_is_an_unreachable_arc_not_slow_traffic():
    with pytest.raises(ValueError, match="unreachable arc"):
        SpeedProfile(bucket_seconds=HOUR, multipliers_ppt=(1000, 0))


def test_a_profile_needs_buckets_with_length():
    with pytest.raises(ValueError, match="positive"):
        SpeedProfile(bucket_seconds=0, multipliers_ppt=(1000,))
    with pytest.raises(ValueError, match="at least one bucket"):
        SpeedProfile(bucket_seconds=HOUR, multipliers_ppt=())


def test_a_zero_length_arc_takes_no_time_at_any_speed():
    """`UC-069`'s two hundred orders at one address, arriving here."""
    assert travel(0, 8 * HOUR, peak_profile()) == 0
    assert fastest_possible(0, peak_profile()) == 0


def test_the_arithmetic_is_integer_and_repeatable():
    """CON-4: two machines must agree about when a driver left."""
    profile = peak_profile()
    once = [travel(n, n * 7, profile) for n in range(1, 400)]
    twice = [travel(n, n * 7, profile) for n in range(1, 400)]

    assert once == twice
    assert all(isinstance(value, int) for value in once)
