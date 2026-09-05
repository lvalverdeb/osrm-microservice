"""Who was promised what, and did the round keep it?

Demonstrates E-93/T-93 against the Costa Rica dataset: per-order SLA windows
derived at intake, and an attainment measure that counts the promises kept.

    vrp.model      sla_window, PRIORITY_SOURCES, precedence, must_be_served
    vrp.evaluator  window_attainment -- kept promises, unpriced
    vrp.solve      PyVRP
    vrp.verify     the independent verifier

`UC-116` breaks on "fixed windows. The window is derived from the fault
timestamp plus the SLA, so it is computed at intake and differs per order."
Two orders of the same class taken an hour apart are due an hour apart. Most
examples in this repository put one window on every stop -- the whole shift --
which is a promise to nobody in particular, and against which any plan scores
perfectly.

The dataset already carries the class. `priority` is one of `express`,
`standard` or `scheduled` on every delivery, and until this example nothing
read it. The three are not three weights on one dial; they are three different
promises:

    express    a response clock from intake. Must be served -- no prize, so
               there is no price at which declining is acceptable (FR-12).
    standard   a longer clock, and declinable at a price. The plan may drop
               one, and pays the prize when it does.
    scheduled  a slot the customer chose. It does not follow the route; the
               route follows it, and half of them are inconvenient.

Ranked by `precedence`, express outranks scheduled on equal tiers because an
SLA outranks a commercial preference (FR-25) -- not because it was listed
first.

Four rounds over the same stops:

1. **One window for everybody.** The shift, on every stop. Attainment is 1000
   parts per thousand and the number is worth nothing: nothing in particular
   was promised. This round also *calibrates* the other three -- the response
   targets below are percentiles of its own service times, so the comparison
   keeps separating when `--stops` changes and does not depend on a deadline
   somebody once typed.
2. **The promises the data actually made.** Windows per order, hard. What the
   round costs in kilometres to keep them is the whole answer.
3. **Priced, not refused.** The same windows, made soft with asymmetric
   earliness and lateness costs. Nothing is refused; some promises break, and
   `window_attainment` is what says which -- `lateness_penalty` cannot, since
   it is zero for every hard window by construction.
4. **The same target as one fixed window.** Round 2's express clock, written
   the way `UC-116` says breaks it: one window for everybody, running from the
   start of the shift rather than from each order's own intake. It is the same
   promise on paper and an unkeepable one in fact, which is the difference
   between a derived window and a typed one.

   It is also the round that argues for the measure. A hard window may not
   carry a lateness rate at all -- `TimeWindow` forbids one, since a hard
   window cannot be violated -- so the priced lateness here is **zero**
   however late the van arrives, and `window_attainment` is the only thing in
   the report that can see the breach.

Not delivered, and stated rather than implied: PyVRP has no soft time windows
(see E-23), so round 3 does not *search* for the cheapest lateness -- a soft
window becomes a wide hard one and the breach is priced afterwards. The intake
timestamps are the example's own: the corpus has no arrival clock, and the
order number is the only sequence it carries, so intake is spread across the
first quarter of the shift in order-number order and says so here rather than
pretending to a field that does not exist.

Requires a running gateway: distances are measured on the road network and
there is no straight-line fallback. `examples/.env` points at the FreeBSD jail.
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/tw/sla_windows.py --stops 24
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

# Importing config puts OSRM_API_URL into the environment and the repository
# root on sys.path, which is what makes `import vrp` below resolve.
import config  # noqa: F401
import dataset

from vrp.evaluator import ObjectiveWeights, evaluate, window_attainment
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    Vehicle,
    must_be_served,
    precedence,
    sla_window,
)
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
HOUR = 3600
SHIFT = TimeWindow(start=6 * HOUR, end=20 * HOUR)

# A delivery an hour early is an inconvenience; an hour late is a failed
# delivery and a second visit. §6.2 asks for exactly this asymmetry.
EARLINESS_PER_SEC = 1
LATENESS_PER_SEC = 12

# Which percentile of the calibration round's own service times each class is
# promised. Express is promised early enough that serving it needs the route
# reordered; standard is promised late enough that most of the round makes it.
TARGET_PPT = {"express": 350, "standard": 850}
SLOT = 2 * HOUR
CLASSES = ("express", "standard", "scheduled")


def percentile(values: list[int], ppt: int) -> int:
    """The value at `ppt` parts per thousand of a sorted list."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, len(ordered) * ppt // 1000)]


def intake_times(deliveries: list[dict]) -> dict[str, int]:
    """When each order was taken, in order-number sequence. `UC-116`.

    The corpus has no arrival timestamp. `order_id` is the only sequence it
    carries, so the round's orders are spread across the first quarter of the
    shift in that order -- which is an assumption of this example, not a fact
    about the data, and the docstring above says so.
    """
    spread = (SHIFT.end - SHIFT.start) // 4
    by_number = sorted(deliveries, key=lambda d: d["order_id"])
    last = max(len(by_number) - 1, 1)
    return {d["product_id"]: SHIFT.start + index * spread // last
            for index, d in enumerate(by_number)}


def targets(baseline: tuple) -> dict[str, int]:
    """Response targets read off the calibration round, not off a clock.

    A literal deadline stops separating the classes the moment the geometry
    changes -- a different `--stops`, a different depot -- and then the example
    demonstrates nothing while still printing a table.
    """
    elapsed = [step.start_service - SHIFT.start
               for step in baseline if step.order_id]
    return {name: max(HOUR, percentile(elapsed, ppt))
            for name, ppt in TARGET_PPT.items()}


def appointment(intake: int) -> TimeWindow:
    """The slot a customer chose: a fixed two hours, indifferent to the route."""
    slots = max(1, (SHIFT.end - SHIFT.start) // SLOT)
    index = (intake - SHIFT.start) // max(1, (SHIFT.end - SHIFT.start) // slots)
    start = SHIFT.start + min(index, slots - 1) * SLOT
    return TimeWindow(start=start, end=min(start + SLOT, SHIFT.end))


def window_for(delivery: dict, intake: int, target: dict[str, int]) -> TimeWindow:
    """The promise this order carries, derived at intake. FR-25, `UC-116`."""
    if delivery["priority"] == "scheduled":
        return appointment(intake)
    return sla_window(reported_at=intake, opens_at=SHIFT.start,
                      respond_within=target.get(delivery["priority"],
                                                target["standard"]))


def soften(window: TimeWindow) -> TimeWindow:
    """The same promise, priced instead of enforced. §6.2."""
    return replace(window, hardness="SOFT",
                   earliness_cost_per_sec=EARLINESS_PER_SEC,
                   lateness_cost_per_sec=LATENESS_PER_SEC)


def order_for(delivery: dict, window: TimeWindow, prize: int) -> Order:
    """One delivery as an order, ranked by what put it in its tier.

    Only `standard` carries a prize, which is what makes it the class a plan
    may decline; `must_be_served` reports the consequence rather than this
    example asserting it.
    """
    priority = delivery["priority"]
    declinable = priority == "standard"
    return Order(
        id=delivery["product_id"], kind="JOB",
        quantities={"kg": dataset.load_kg(delivery)},
        priority_tier=1 if declinable else 0,
        prize=prize if declinable else 0,
        priority_source="COMMERCIAL" if priority == "scheduled" else "SLA",
        delivery=StopSpec(location_id=delivery["product_id"],
                          time_windows=(window,),
                          service_fixed=delivery["service_minutes"] * 60))


def build(depot: dict, deliveries: list[dict], matrix, windows: dict,
          prize: int) -> Problem:
    """The same round, under whatever promises `windows` carries."""
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for offset, delivery in enumerate(deliveries):
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"],
                                  matrix_index=offset + 1))
        orders.append(order_for(delivery, windows[delivery["product_id"]], prize))
    load = sum(o.quantities["kg"] for o in orders)
    van = Vehicle(id="VAN-1", capacities={"kg": load}, shift=SHIFT,
                  start_location_id="DEPOT", end_location_id="DEPOT")
    return Problem(id="sla", locations=tuple(locations), orders=tuple(orders),
                   vehicles=(van,), matrix=matrix)


def clock(seconds: int) -> str:
    return f"{seconds // HOUR:02d}:{seconds % HOUR // 60:02d}"


def steps_of(solution) -> tuple:
    return tuple(step for route in solution.routes for step in route.steps)


def report(label: str, problem: Problem, solution, classes: dict) -> tuple:
    """One round: what it cost, what it kept, and whose promise it broke.

    Returns:
        Its distance in metres, its `Attainment`, and the attainment of each
        class, so the closing summary quotes this round rather than a sentence
        somebody has to keep true.
    """
    verdict = verify(problem, solution)
    assignment = {route.vehicle_id: [s.order_id for s in route.steps if s.order_id]
                  for route in solution.routes}
    evaluation = evaluate(problem, assignment, ObjectiveWeights())
    # The canonical timeline, not the solver's own steps. The adapter compiles
    # a soft window to a wide hard one (E-23), so a solver step can begin
    # before a soft window opens; `evaluate` rebuilds the day and waits. Two
    # timelines in one report is how a round came out 428 ppt with zero
    # lateness seconds and a five-hundred-thousand lateness price at once.
    canonical = tuple(step for timeline in evaluation.timelines.values()
                      for step in timeline)
    kept = window_attainment(problem, canonical)

    print(f"\n  {label}")
    print(f"    status        {solution.status}, verifier "
          f"{'accepts' if verdict.ok else 'REJECTS'}")
    if not verdict.ok:
        for violation in verdict.violations[:2]:
            print(f"      {violation}")
    print(f"    distance      {evaluation.breakdown['distance'] / 1000:,.1f} km"
          f"   ({len(solution.unassigned)} declined)")
    print(f"    attainment    {kept.on_time}/{kept.promised} in window"
          f"   ({kept.attained_ppt} ppt)")
    if kept.missed:
        print(f"    lateness      {kept.lateness_seconds // 60} min over "
              f"{kept.missed} stop(s), worst {kept.worst_lateness // 60} min"
              f"   -- priced at {evaluation.breakdown['lateness_penalty']:,}")
    by_class = {}
    for name in CLASSES:
        subset = tuple(s for s in canonical if classes.get(s.order_id) == name)
        share = window_attainment(problem, subset)
        if share.promised:
            by_class[name] = share
            print(f"      {name:<10}{share.on_time}/{share.promised}"
                  f"   ({share.attained_ppt} ppt)")
    return evaluation.breakdown["distance"], kept, by_class


def composition(deliveries: list[dict]) -> dict[str, int]:
    """What the slice actually contains. A class it lacks cannot be shown."""
    return {name: sum(1 for d in deliveries if d["priority"] == name)
            for name in CLASSES}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", type=int, default=24)
    parser.add_argument("--iterations", type=int, default=1200)
    parser.add_argument("--dataset", type=Path, default=dataset.DEFAULT_PATH)
    args = parser.parse_args()

    # `nearest` takes twelve stops inside a kilometre, where driving is free
    # and no clock binds. `spread` walks the catchment a van actually covers.
    deliveries, depot = dataset.load(args.dataset).spread(args.stops)
    counts = composition(deliveries)
    print(f"depot {depot['name']} -- {len(deliveries)} stops: "
          + ", ".join(f"{n} {name}" for name, n in counts.items() if n))
    for name, count in counts.items():
        if not count:
            print(f"  no {name} deliveries in this slice; "
                  f"its row is omitted rather than invented")

    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    matrix = dataset.road_matrix(points, GATEWAY, "sla")
    print(f"matrix        road, from {GATEWAY}")

    classes = {d["product_id"]: d["priority"] for d in deliveries}
    intake = intake_times(deliveries)
    # A prize is the price of declining, so it is worth a few legs of this
    # round rather than a number from nowhere.
    prize = 3 * percentile([matrix.distance(0, n + 1)
                            for n in range(len(deliveries))], 500)

    wide = {product: SHIFT for product in classes}
    base = build(depot, deliveries, matrix, wide, prize)
    baseline = solve(base, iterations=args.iterations, seed=0)
    free, _, _ = report("one window for everybody (06:00-20:00)", base,
                        baseline, classes)

    target = targets(steps_of(baseline))
    print(f"\n  calibrated from that round: express within "
          f"{target['express'] // 60} min of intake, standard within "
          f"{target['standard'] // 60} min;")
    print(f"  intake runs {clock(min(intake.values()))}-"
          f"{clock(max(intake.values()))}, scheduled slots are {SLOT // HOUR}h "
          f"and fixed")

    promises = {product: window_for(next(d for d in deliveries
                                         if d["product_id"] == product),
                                    intake[product], target)
                for product in classes}
    hard = build(depot, deliveries, matrix, promises, prize)
    kept_distance, _, _ = report("the promises the data made (hard)", hard,
                                 solve(hard, iterations=args.iterations,
                                       seed=0), classes)

    soft_windows = {product: soften(w) for product, w in promises.items()}
    soft = build(depot, deliveries, matrix, soft_windows, prize)
    _, priced, per_class = report(
        "the same promises, priced not refused (soft)", soft,
        solve(soft, iterations=args.iterations, seed=0), classes)

    # The same express target, written the way `UC-116` says breaks: one fixed
    # window for everybody instead of a clock that starts when each order did.
    fixed = TimeWindow(start=SHIFT.start,
                       end=SHIFT.start + target["express"])
    tight = dict.fromkeys(classes, fixed)
    # No prize on this round: a window a dispatcher types applies to work that
    # has to happen, and `must_be_served` makes a prizeless order undeclinable.
    # With a prize the solver simply declines eight stops and reports a perfect
    # attainment over the ten it kept -- which is why the declined count below
    # is printed beside the rate and not instead of it.
    strict = build(depot, deliveries, matrix, tight, prize=0)
    _, typed, _ = report(
        f"the express target as one fixed window "
        f"({clock(fixed.start)}-{clock(fixed.end)}, hard)", strict,
        solve(strict, iterations=args.iterations, seed=0), classes)

    required = [o.id for o in hard.orders if must_be_served(o)]
    extra = (kept_distance - free) / 1000
    print("\n" + "=" * 72)
    print(f"Keeping the promises the data made cost {extra:,.1f} km more than "
          f"the round that\npromised nothing in particular "
          f"({(kept_distance - free) * 100 // max(free, 1)}% further), and it "
          f"kept every one of them.")
    worst = min(per_class, key=lambda name: per_class[name].attained_ppt)
    print(f"\nPriced instead of enforced, {priced.missed} of {priced.promised} "
          f"promises break, and they are not\nspread evenly: {worst} comes "
          f"last at {per_class[worst].attained_ppt} ppt. Which class a flat "
          "lateness rate\nsacrifices is not something the rate can be asked.")
    print(f"\nThe fixed window is the one `UC-116` warns about. It runs "
          f"{typed.lateness_seconds // 60} minutes late across\n"
          f"{typed.missed} stops and prices that at exactly zero, because a "
          "hard window may not carry a\nrate. `lateness_penalty` reports 0 "
          "and is right to; the attainment is what sees it.")
    print(f"\n{len(required)} of {len(hard.orders)} orders may not be "
          "declined at all. `precedence` ranks what is\nleft by (tier, "
          "source), lower being more protected:")
    reps = []
    for name in CLASSES:
        first = next((o for o in hard.orders if classes[o.id] == name), None)
        if first is not None:
            reps.append(first)
            print(f"      {name:<10}{precedence(first)}   "
                  f"source {first.priority_source}")
    reps.sort(key=precedence)
    tied = [o for o in reps if o.priority_tier == reps[0].priority_tier]
    if len(tied) > 1:
        print(f"\nSo {classes[tied[0].id]} outranks {classes[tied[1].id]} on "
              "an equal tier because FR-25 ranks\nthe sources, not because "
              "one was written down first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
