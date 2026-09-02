"""Leaving later must never mean arriving earlier.

Demonstrates time-dependent travel landed for E-40/T-40 (FR-14, §6.3, MTX-9):

    vrp.timedependent  the Ichoua–Gendreau–Potvin construction, and its bound

§6.3 requires one implementation and forbids another in the same breath:
"Per-arc piecewise-constant **speed** profiles over time buckets...
Piecewise-constant *travel time* per bucket violates FIFO and MUST NOT be used;
the Ichoua–Gendreau–Potvin construction (speed changes when a bucket boundary
is crossed mid-arc) is the required formulation."

The forbidden version is one line shorter. This shows what that line buys.

Three things, in order:

1. **The overtaking.** Charging a whole arc at the rate in force when the van
   left lets 09:59 arrive after 10:01, because the earlier van pays the peak
   rate for a journey that spends one minute in the peak. A driver can watch
   that happen and know the plan is wrong.

2. **The construction.** Changing speed at the boundary instead. Leaving later
   means being behind at every instant, so arriving earlier is arithmetically
   impossible rather than merely unlikely.

3. **The bound §6.3 filters with.** The arc at the best speed the profile ever
   offers — never beaten by a real departure, so it is safe to prune with, and
   worth reporting how often it fails to prune something that turns out
   infeasible anyway.

**The profiles here are invented and nothing pretends otherwise.** `T-40` is
the construction; fitting profiles that resemble a real afternoon is `T-63`,
which needs telematics volume this stack does not have. That separation is why
`T-40` was buildable: its definition of done — the FIFO property and the
filter's false-negative rate — asks about arithmetic, not about traffic.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/time_dependent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

import random

from vrp.timedependent import (
    PPT,
    SpeedProfile,
    arrival,
    fastest_possible,
    filter_moves,
    travel,
)

HOUR = 3600
DAY = 24 * HOUR


def peak() -> SpeedProfile:
    """Free flow, except half speed through a three-hour morning peak."""
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if 7 <= hour <= 9 else 1000
                              for hour in range(24)))


def clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def the_overtaking() -> None:
    heading("1.", "What the forbidden formulation does")
    profile, arc = peak(), 2 * HOUR

    def banned(depart: int) -> int:
        """One flat rate for the whole arc, chosen at the moment of departure."""
        return depart + arc * PPT // profile.multiplier_at(depart)

    print("\n   a two-hour arc, left just before and just after the peak ends:\n")
    print(f"      {'depart':8s} {'banned arrives':16s} required arrives")
    for depart in (10 * HOUR - 60, 10 * HOUR + 60):
        print(f"      {clock(depart):8s} {clock(banned(depart)):16s} "
              f"{clock(arrival(arc, depart, profile))}")
    print("\n   The banned version has the 09:59 van arriving after the 10:01")
    print("   one: it pays the peak rate for a journey that spends a single")
    print("   minute in the peak. That is the no-passing property broken, and")
    print("   a driver can watch it happen.")


def the_construction() -> None:
    heading("2.", "What changing speed at the boundary costs")
    profile = peak()
    print("\n   a one-hour free-flow arc, left at each hour:\n")
    print(f"      {'depart':8s} {'takes':>8s}   arrive")
    for hour in (5, 6, 7, 8, 9, 10):
        depart = hour * HOUR
        took = travel(HOUR, depart, profile)
        print(f"      {clock(depart):8s} {took // 60:5d} min   "
              f"{clock(depart + took)}")
    crossing = travel(HOUR, 6 * HOUR + 1800, profile)
    print(f"\n   and one that straddles the boundary, leaving at 06:30: "
          f"{crossing // 60} min")
    print("   -- half an hour at free flow covers half the arc, and the rest")
    print("   takes an hour at half speed. Neither flat rate gives that.")


def the_bound() -> None:
    heading("3.", "The bound, and what it fails to catch")
    profile = peak()
    rng = random.Random(40)
    moves = []
    for _ in range(2_000):
        free_flow = rng.randint(60, 3 * HOUR)
        depart = rng.randrange(0, DAY)
        moves.append((free_flow, depart, depart + rng.randint(60, 3 * HOUR)))

    report = filter_moves(moves, profile)
    print(f"\n   a two-hour arc is never faster than "
          f"{fastest_possible(2 * HOUR, profile) // 60} min, whenever you leave")
    print(f"\n      considered      {report.considered}")
    print(f"      pruned by bound {report.pruned:5d}  "
          f"({report.pruned_share_ppt / 10:.1f}%)")
    print(f"      passed, then rejected exactly {report.passed_then_rejected:5d}")
    print(f"      false-negative rate {report.false_negative_rate_ppt / 10:.1f}% "
          "of the infeasible moves")
    print("\n   The bound never prunes a move that would have worked -- that is")
    print("   a correctness property, and a bound that is occasionally")
    print("   optimistic discards a plan nobody ever learns was available.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-14 and §6.3. The profiles are invented; T-63 fits real ones.")
    the_overtaking()
    the_construction()
    the_bound()
    print(f"\n{'=' * 72}")
    print("§6.3 forbids bucketing travel time and requires bucketing speed.")
    print("The difference is one line of arithmetic and a property a driver")
    print("can check from the cab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
