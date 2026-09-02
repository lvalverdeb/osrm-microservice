"""Knowing the traffic is not the same as planning around it.

Demonstrates the planning path landed for E-82/T-82 (FR-14, §7.5, NFR-01):

    vrp.polish.tsptw_sequence   the route DP, now charging each leg at the
                                moment the state says the van leaves

`T-80` taught the evaluator and the verifier to agree about a congested day.
That is worth having and it changes no decision: a plan built at free flow and
then priced through the peak is still the free-flow plan, now with accurate
bad news attached. This is the other half -- building the sequence under the
profile, so the peak is something the search routes around rather than
something it discovers afterwards.

Four things, in order:

1. **The two plans.** Five stops on a road, one of them due by 11:00. Free
   flow says visit them outward, nearest first; the peak says go to the far
   one first. Both are optimal for the day they were planned against.

2. **What separates them.** The free-flow plan is *faster* and misses the
   deadline by twenty-one minutes. The congestion-aware plan finishes later on
   the clock and is the only one that can be driven. "Better" here means legal.

3. **§7.5's filter.** The DP dismisses a candidate on the arc at the best speed
   the profile ever offers before pricing the real one. Shown to be a bound
   rather than a guess, because a bound that over-states silently discards
   plans nobody learns were available.

4. **What it costs.** Every arc goes through IGP instead of a table lookup.
   Measured at the DP's `MAX_DP_STOPS` ceiling, against NFR-01.

**The profile is invented and nothing pretends otherwise.** `T-63` fits real
ones from telematics this stack does not have. The dependency ran the other
way than the backlog assumed: a search that carries a profile is testable with
any profile, and only its *usefulness* waits on real traffic.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/planning_under_congestion.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.evaluator import build_timeline
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
    travel_between,
)
from vrp.polish import MAX_DP_STOPS, _floor, tsptw_sequence
from vrp.timedependent import SpeedProfile

HOUR = 3600
DEADLINE = 11 * HOUR


def peak() -> SpeedProfile:
    """Free flow, except half speed through a three-hour morning peak."""
    return SpeedProfile(
        bucket_seconds=HOUR,
        multipliers_ppt=tuple(500 if 7 <= hour <= 9 else 1000
                              for hour in range(24)))


def a_round(profile: SpeedProfile | None, stops: int = 5,
            hop: int = 1800, service: int = 300) -> Problem:
    """Stops on a road out of the depot, the far one due by 11:00."""
    day = TimeWindow(start=0, end=20 * HOUR)
    due = TimeWindow(start=0, end=DEADLINE)
    locations = tuple(
        Location(id="D" if i == 0 else f"C{i}", lat=9.9 + i / 50, lon=-84.0,
                 matrix_index=i)
        for i in range(stops + 1))
    grid = tuple(tuple(abs(i - j) * hop for j in range(stops + 1))
                 for i in range(stops + 1))
    orders = tuple(
        Order(id=f"O{i}", kind="JOB", quantities={"kg": 1},
              delivery=StopSpec(location_id=f"C{i}", service_fixed=service,
                                time_windows=((due,) if i == stops
                                              else (day,))))
        for i in range(1, stops + 1))
    return Problem(
        id=f"congestion-{profile is not None}-{stops}",
        locations=locations, orders=orders,
        vehicles=(Vehicle(id="V1", capacities={"kg": 100},
                          shift=TimeWindow(start=7 * HOUR, end=20 * HOUR),
                          start_location_id="D", end_location_id="D"),),
        matrix=TravelMatrix(version="congestion", durations=grid,
                            distances=grid),
        speed_profile=profile)


def clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def driven_through_the_peak(sequence: list[str]) -> list:
    """What actually happens to a sequence when the day is congested."""
    return build_timeline(a_round(peak()), "V1", sequence)


def the_two_plans() -> tuple[list[str], list[str]]:
    heading("1.", "The same five stops, sequenced against two different days")
    aware = tsptw_sequence(a_round(peak()), "V1",
                           [f"O{i}" for i in range(1, 6)])
    blind = tsptw_sequence(a_round(None), "V1",
                           [f"O{i}" for i in range(1, 6)])
    print(f"\n   C5 is the far end of the road and is due by {clock(DEADLINE)}.")
    print(f"   The van leaves the depot at {clock(7 * HOUR)}, and the peak")
    print("   runs 07:00-10:00 at half speed.\n")
    print(f"      planned at free flow : {' -> '.join(blind)}")
    print(f"      planned under peak   : {' -> '.join(aware)}")
    print("\n   Nearest-first is right for an empty road. Under the peak the")
    print("   far stop has to be bought first, while the day is still young.")
    return aware, blind


def what_separates_them(aware: list[str], blind: list[str]) -> None:
    heading("2.", "Faster, and undriveable")
    print(f"\n      {'plan':22s} {'C5 served':>10s}  {'finishes':>9s}  verdict")
    for label, sequence in (("planned under peak", aware),
                            ("planned at free flow", blind)):
        timeline = driven_through_the_peak(sequence)
        served = next(step.start_service for step in timeline
                      if step.order_id == "O5")
        late = served > DEADLINE
        print(f"      {label:22s} {clock(served):>10s}  "
              f"{clock(timeline[-1].arrival):>9s}  "
              f"{'LATE by ' + str((served - DEADLINE) // 60) + ' min' if late else 'in window'}")
    print("\n   The free-flow plan is the quicker of the two and is the one")
    print("   that breaks a promise. A search told only about distance cannot")
    print("   see the difference, because at free flow there is none.")


def the_filter() -> None:
    heading("3.", "The bound the DP prunes with, and why it may not be a guess")
    problem = a_round(peak())
    span = range(7 * HOUR, 20 * HOUR, 1800)
    print("\n   arc D->C5 priced at each departure, against the bound:\n")
    print(f"      {'depart':8s} {'costs':>8s} {'bound':>8s}   bound holds")
    bound = _floor(problem, 0, 5)
    for depart in list(span)[:8]:
        exact = travel_between(problem, 0, 5, depart)
        print(f"      {clock(depart):8s} {exact // 60:5d} min {bound // 60:5d} min"
              f"   {'yes' if bound <= exact else 'NO'}")
    worst = min(travel_between(problem, 0, 5, d) - bound for d in span)
    print(f"\n   Tightest margin across the whole day: {worst // 60} min.")
    print("   The bound is never beaten, so pruning on it discards only")
    print("   candidates the exact price would have discarded too. A bound")
    print("   that over-states by one second throws away legal plans and")
    print("   leaves no trace that it did.")


def what_it_costs() -> None:
    heading("4.", "NFR-01: what charging every arc through IGP costs")
    print(f"\n   the route DP at its ceiling of {MAX_DP_STOPS} stops:\n")
    print(f"      {'day':12s} {'sequencing':>12s}")
    times = {}
    for label, profile in (("free flow", None), ("peak", peak())):
        problem = a_round(profile, stops=MAX_DP_STOPS, hop=600, service=120)
        orders = [order.id for order in problem.orders]
        start = time.perf_counter()
        tsptw_sequence(problem, "V1", orders)
        times[label] = time.perf_counter() - start
        print(f"      {label:12s} {times[label] * 1000:9.0f} ms")
    print(f"\n   A factor of {times['peak'] / times['free flow']:.1f}x on a step that is already")
    print(f"   bounded at {MAX_DP_STOPS} stops -- a constant, not a change of")
    print("   growth. NFR-01 gives 15 minutes for 2,000 stops and this is")
    print("   under a second per route.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nFR-14, §7.5 and NFR-01. The profile is invented; T-63 fits real ones.")
    aware, blind = the_two_plans()
    what_separates_them(aware, blind)
    the_filter()
    what_it_costs()
    print(f"\n{'=' * 72}")
    print("Evaluating under congestion tells you the plan is late.")
    print("Planning under it gives you one that is not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
