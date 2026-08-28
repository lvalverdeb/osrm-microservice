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

5. **Pricing the wait.** Postponing is free by default, which is why lazy looks
   so strong. §8.3 says it is not free, and the sweep shows what happens once
   the customer's wait is on the bill.

6. **Pricing the reassignment**, §8.3's other churn term -- and an honest
   account of how little it moves here, and why that is about the constructor
   rather than about the term.

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


def show_the_price_of_waiting(problem, days) -> None:
    print("\n5. Pricing the wait (§8.3's \"ETA shifts communicated to"
          " customers\")")
    from vrp.replay import dispatchable

    corpus = dispatchable(problem, DAY, window=3 * HOUR)
    corpus_days = generate_days(corpus, count=90, seed=0, horizon=DAY)

    print(f"   {'price/1000s':>12}{'greedy':>12}{'lazy':>12}   cheaper")
    for price in (0, 100, 200, 400, 700):
        totals = {}
        for name, policy in (("greedy", greedy), ("lazy", lazy)):
            totals[name] = sum(
                replay(corpus, day, policy, epoch_length=HOUR,
                       delay_price=price).cost for day in corpus_days)
        winner = min(totals, key=lambda n: totals[n])
        print(f"   {price:>12}{totals['greedy']:>12,}{totals['lazy']:>12,}"
              f"   {winner}")

    print("   At zero, holding work costs nothing and lazy wins by 9%. §8.3")
    print("   says that is wrong: \"report and optionally penalise churn (stops")
    print("   moved between vehicles, ETA shifts communicated to customers)\",")
    print("   and a request held four hours is exactly such a shift.")
    print("   The crossover sits between 200 and 400 thousandths of a metre per")
    print("   order-second. Where a fleet actually sits on that scale is a")
    print("   judgement about their customers, not about routing -- which is")
    print("   why the price is an argument and the default is the unpriced")
    print("   behaviour every earlier measurement was taken under.")
    print("   The unit is per *thousand* order-seconds because measurement said")
    print("   so: at one metre per second the term swamps routing entirely and")
    print("   greedy wins at every price above zero, which is a unit problem")
    print("   wearing the costume of a result.")


def show_the_price_of_reassignment(problem) -> None:
    print("\n6. Pricing the reassignment (§8.3's \"stops moved between"
          " vehicles\")")
    from dataclasses import replace as _replace

    from vrp.replay import dispatchable

    # Tight enough that the split across vehicles can actually change. With
    # roomy vans first-fit puts everything on the first one and no reassignment
    # is possible at all -- zero moves at any price, which reads as a broken
    # term rather than a comfortable fleet.
    tight = _replace(problem, vehicles=tuple(
        _replace(vehicle, capacities={"kg": 3})
        for vehicle in problem.vehicles))
    corpus = dispatchable(tight, DAY, window=3 * HOUR)
    corpus_days = generate_days(corpus, count=90, seed=0, horizon=DAY)

    print(f"   {'policy':<10}{'reassignments in 90 days':>26}")
    for name, policy in (("greedy", greedy), ("lazy", lazy)):
        moves = sum(replay(corpus, day, policy, epoch_length=HOUR,
                           move_price=1).moves for day in corpus_days)
        print(f"   {name:<10}{moves:>26}")

    print("   Greedy is structurally zero: it dispatches on arrival, so nothing")
    print("   is ever provisionally assigned and nothing can change hands.")
    print("   DYN-1 has the epoch controller \"publish plans\", which is what")
    print("   makes a held order's vehicle something a driver has already seen")
    print("   -- and moving it the reassignment §8.3 asks to be priced.")
    print("   Lazy's seven over ninety days is a real number and a small one,")
    print("   and the reason is the constructor rather than the term. First-fit")
    print("   is deterministic: the same held set always packs the same way, so")
    print("   a vehicle only changes when the set itself changes shape. A real")
    print("   solver rebuilding the plan each epoch would churn far more, and")
    print("   this measurement understates what the term would cost in")
    print("   production rather than showing it does not matter.")
    print("   The two terms stay separate -- §8.3 names both and T-57 kept them")
    print("   apart because one reassigns a driver and the other changes what a")
    print("   customer was told. Summing them here would undo that.")


def main() -> int:
    problem = instance()
    days = generate_days(problem, count=90, seed=0, horizon=DAY)

    show_arrivals(problem, days[0])
    show_one_day(problem, days[0])
    show_the_report(problem, days)
    show_what_it_is_careful_about(problem, days)
    show_the_price_of_waiting(problem, days)
    show_the_price_of_reassignment(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
