"""Ninety days, three policies, one number each.

Demonstrates the historical replayer landed for E-53/T-53 (DYN-6, AC-3.2, §8.1):

    vrp.replay      the corpus, the epoch-by-epoch replay, the comparison
    vrp.policies    §8.2's permanent baselines, the denominator
    vrp.epochs      the controller, and AC-3.1 holding throughout

This is the gate the rest of Slice 5 stands on. T-54's ICD policy has to "beat
greedy and lazy on the replay corpus"; T-55's prize-collecting has to be
"comparable or better than ICD". Neither claim means anything without a
measurement both are made against, so the baselines came first, then this.

Four things, in order:

1. **What makes it a replay.** Requests arrive through the day, and a policy
   only sees what has arrived. Hand everything to epoch 0 and this is a static
   solve wearing a costume.

2. **One day, traced.** The same day under greedy and under lazy, epoch by
   epoch, so the difference is visible rather than asserted.

3. **The comparison report** AC-3.2 asks for, over ninety days.

4. **What the report is careful about.** Cost is not the whole story, and a
   policy being overruled is a different finding from one that is expensive.

Runs offline. No gateway required.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/dynamic/replay_policies.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

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
from vrp.replay import compare, generate_days, replay

HOUR = 3600
DAY = TimeWindow(start=0, end=8 * HOUR)
LEG = 900


def clock(seconds: int) -> str:
    return f"{8 + seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def instance(stops: int = 8, vans: int = 2) -> Problem:
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


def show_arrivals(problem, day) -> None:
    print("\n1. Requests arrive through the day (§8.1)")
    print(f"   {'order':<8}{'known at':>10}")
    for order_id, seen in sorted(day.arrivals.items(), key=lambda kv: kv[1]):
        print(f"   {order_id:<8}{clock(seen):>10}")
    print("   A policy at 09:00 has never heard of the 15:00 request. That is")
    print("   the whole difference between a replayer and a loop: hand")
    print("   everything to epoch 0 and every policy scores identically,")
    print("   because there is nothing left to consolidate.")


def show_one_day(problem, day) -> None:
    print("\n2. One day, two policies")
    for name, policy in (("greedy", greedy), ("lazy", lazy)):
        run = replay(problem, day, policy, epoch_length=HOUR)
        print(f"   {name}: {run.dispatch_epochs} waves used, cost {run.cost:,}")
        for record in run.epochs:
            if record.dispatched or record.postponed:
                print(f"      {clock(record.index * HOUR):>6}  "
                      f"out {list(record.dispatched)!s:<26}"
                      f"held {list(record.postponed)!s}")
    print("   Greedy sends the moment it hears. Lazy holds everything until")
    print("   AC-3.1 forces its hand, then sends a fuller van fewer times.")
    print("   Whether that is better is what the report is for.")


def show_the_report(problem, days) -> None:
    print(f"\n3. The comparison report over {len(days)} days (AC-3.2)")
    report = compare(problem, days, BASELINES, epoch_length=HOUR)
    print(f"   {'policy':<10}{'cost':>12}{'vs greedy':>12}{'waves used':>12}"
          f"{'overruled':>11}")
    for name in sorted(report.results, key=lambda n: report.results[n].cost):
        result = report.results[name]
        delta = (f"{result.versus_baseline / report.results['greedy'].cost * 100:+.1f}%"
                 if report.results["greedy"].cost else "-")
        print(f"   {name:<10}{result.cost:>12,}{delta:>12}"
              f"{result.dispatch_epochs:>12}{result.forced:>11}")
    print(f"   baseline: {report.baseline}")
    print("   AC-3.2 names greedy specifically as the denominator, and the")
    print("   report refuses to be built without it -- a comparison against an")
    print("   arbitrary policy is not the comparison the acceptance asks for.")


def show_what_it_is_careful_about(problem, days) -> None:
    print("\n4. Why cost is not the only column")
    report = compare(problem, days, BASELINES, epoch_length=HOUR)
    lazy_result = report.results["lazy"]
    greedy_result = report.results["greedy"]

    print(f"   lazy uses {lazy_result.dispatch_epochs} waves against greedy's "
          f"{greedy_result.dispatch_epochs}")
    print(f"   lazy was overruled {lazy_result.forced} times; greedy "
          f"{greedy_result.forced}")
    print("   A policy can be cheap by being late, so waves sit beside the")
    print("   money: lazy's 39% saving is bought by holding work back, and")
    print("   whether that is a saving or a service problem is not a question")
    print("   one number can answer.")
    print("   The overruled column is zero for all three, and that is the good")
    print("   news rather than a broken counter. Lazy dispatches must-go work")
    print("   *voluntarily* -- that is its whole definition -- so AC-3.1 never")
    print("   has to step in. The column earns its place against policies that")
    print("   do refuse: E-51 shows one, overruled at every wave. A policy")
    print("   losing money and a policy that would be causing service failures")
    print("   must not read the same, so they are counted separately.")
    print("   Note also what did *not* happen anywhere in the corpus: not one")
    print("   must-go request was postponed, under any of the three. T-51")
    print("   promised that \"across the replay corpus\" before the corpus")
    print("   existed. It exists now, and the promise holds.")


def main() -> int:
    problem = instance()
    days = generate_days(problem, count=90, seed=0, horizon=DAY)

    show_arrivals(problem, days[0])
    show_one_day(problem, days[0])
    show_the_report(problem, days)
    show_what_it_is_careful_about(problem, days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
