"""Four inspections a year is not four inspections in January.

Demonstrates the multi-period horizon landed for E-73/T-73 (FR-23, §12.2):

    vrp.periodic   `Recurrence`, `schedule`, `compliance`
    vrp.consistency  T-47's `Horizon`, which measures the plans that result

FR-23: "Support a **multi-period planning horizon**: recurring visits planned
across several periods as one problem rather than as independent days, with
per-order visit frequency, permitted-day patterns, and compliance measured
against the interval rather than the day."

Seven operations asked for it and they agree on where the coupling is.
`UC-043`: "The decision is which days to visit which assets; optimising each
day independently makes the cycle infeasible." `UC-024`: "The unit of
optimisation is the month; a locally optimal Tuesday leaves an infeasible
Friday." So the days are chosen first, and each day is then an ordinary
single-day problem for the ordinary solver.

Three things, in order:

1. **What a day-at-a-time planner does.** Travel cost alone prefers to cluster
   recurring work: visiting the same four sites on four consecutive days is
   cheaper than spreading them over a month. It is also a month out of
   compliance, and no single day's plan is wrong.

2. **What the interval means.** `UC-129`: "the next due date is set by the last
   visit, so today's plan determines next year's feasible plan." The measure is
   the longest gap between visits *including the gaps at each end* -- because
   the eleven months after the last January inspection are the failure.

3. **What a contract nobody can keep looks like.** Five visits on two permitted
   days is not a shortfall to report at the end of the month. It is refused
   when the schedule is built, because a dispatcher told "non-compliant" will
   go looking for a fleet that would fix it.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/multi_period.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.periodic import Recurrence, compliance, schedule

HORIZON = 28


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def calendar(days: tuple[int, ...], horizon: int = HORIZON) -> str:
    return "".join("X" if day in days else "." for day in range(horizon))


def what_a_daily_planner_does() -> None:
    heading("1.", "What a day-at-a-time planner does")
    quarterly = Recurrence("EXTINGUISHER", visits=4)
    spread = compliance(schedule([quarterly], HORIZON), [quarterly])

    clustered = {day: (["EXTINGUISHER"] if day < 4 else [])
                 for day in range(HORIZON)}
    packed = compliance(clustered, [quarterly])

    print(f"\n   four inspections over {HORIZON} days, day 0 on the left:\n")
    for label, report in (("clustered", packed["EXTINGUISHER"]),
                          ("spread   ", spread["EXTINGUISHER"])):
        row = report
        print(f"      {label}  {calendar(row.days)}  "
              f"visits {len(row.days)}  worst gap {row.worst_interval:2d}")
    print("\n   Both made four visits. Travel cost alone prefers the first,")
    print("   because four sites on four consecutive days is cheaper than four")
    print("   sites a week apart -- and no single day's plan is wrong.")


def what_the_interval_means() -> None:
    heading("2.", "What the interval means")
    monthly = Recurrence("INSPECTION", visits=2, max_interval=20)
    early = {day: (["INSPECTION"] if day in (0, 1) else [])
             for day in range(HORIZON)}
    report = compliance(early, [monthly])["INSPECTION"]

    print(f"\n   two visits, allowed {monthly.max_interval} days apart:\n")
    print(f"      {calendar(report.days)}")
    print(f"\n      visits made:    {len(report.days)} of {report.required}"
          f"   -> met: {report.visits_met}")
    print(f"      worst gap:     {report.worst_interval:2d} of "
          f"{report.allowed_interval}   -> met: {report.interval_met}")
    print(f"      commitment kept: {report.met}")
    print("\n   The gap between the two visits is one day. The gap after the")
    print("   second is twenty-seven, and that is the one the contract is")
    print("   broken by. A measure looking only between visits calls this")
    print("   perfect.")


def a_contract_nobody_can_keep() -> None:
    heading("3.", "A contract nobody can keep")
    impossible = Recurrence("WEEKLY", visits=5,
                            permitted_days=frozenset({0, 7}))
    try:
        schedule([impossible], HORIZON)
    except ValueError as refusal:
        print(f"\n   five visits, two permitted days:\n      {refusal}")

    too_tight = Recurrence("TIGHT", visits=2, max_interval=3,
                           permitted_days=frozenset({0, 1}))
    try:
        schedule([too_tight], HORIZON)
    except ValueError as refusal:
        print(f"\n   two visits three days apart, both due at the start:"
              f"\n      {refusal}")
    print("\n   Neither is a planning shortfall, so neither is reported as one.")
    print("   A dispatcher told 'non-compliant' goes looking for a fleet that")
    print("   would fix it, and no fleet fixes a contract that cannot be kept.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-23, from CAT-VRP-003 §12.2 -- seven operations asked for it.")
    what_a_daily_planner_does()
    what_the_interval_means()
    a_contract_nobody_can_keep()
    print(f"\n{'=' * 72}")
    print("The unit of optimisation is the horizon, and the decision it makes")
    print("is which days to visit which assets. Each day is an ordinary problem")
    print("once that is settled -- the coupling is entirely in the calendar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
