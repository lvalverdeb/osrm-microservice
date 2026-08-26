"""A long-haul day under EU and US driving law, and what the law costs.

Demonstrates the hours-of-service rules engine landed for E-25/T-25 against the
Costa Rica dataset and real road distances:

    vrp.hos.rules     EU-561 and US-HOS, as a pluggable rule set
    vrp.hos.schedule  break insertion *inside* route evaluation
    vrp.verify        INV-7, recomputed from the timeline by code that shares
                      nothing with the scheduler

Three things it shows, in order:

1. **The same day is legal in one jurisdiction and not the other.** EU-561 caps
   driving at 9 h and forces 45 minutes off after 4.5 h; US-HOS allows 11 h and
   asks 30 minutes after 8 h. A run that fits comfortably in Texas can be
   unlawful in Portugal, which is why FR-15 makes the rule set pluggable rather
   than a constant.

2. **Breaks are not free, and not appended.** Every arrival after a break moves
   later by its duration. §6.4 requires break insertion to be a scheduling
   subproblem inside evaluation precisely because a post-processing pass gets
   this wrong, and its symptom is a plan that "loses" its last stops on
   publication rather than one that reports itself infeasible.

3. **A tired driver is a different problem.** The same route, planned for
   someone who already drove six hours, is a compliance incident rather than an
   optimisation gap -- §6.4's words. `--already-drove` shows it failing.

The verifier is the judge throughout. It rebuilds the driver's day from the
timeline's own arrival and departure stamps -- the reading a tachograph would
take -- so agreement here is two implementations of one regulation concurring,
not one implementation agreeing with itself.

Requires a running gateway with an engine behind it; `examples/.env` already
points at the FreeBSD jail. Pass --straight-line to skip it.
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/hours_of_service.py
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/rich/hours_of_service.py --stops 3 --already-drove 6

The default is three of the furthest stops, which is about 10.5 hours of
driving: unlawful under EU-561 and lawful under US-HOS. Raising --stops pushes
past both limits, which shows refusal but not the difference between them.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# Importing config puts OSRM_API_URL into the environment and the repository
# root on sys.path, which is what makes `import vrp` below resolve.
import config  # noqa: F401
import httpx

from vrp.hos import EU_561, US_HOS, DriverState, HoursOfServiceRules
from vrp.hos.schedule import schedule_route
from vrp.model import (
    Location,
    Order,
    Problem,
    Route,
    Solution,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DATASET = Path("data/deliveries_cr.json")
HOUR = 3600
SHIFT = TimeWindow(start=0, end=24 * HOUR)


def great_circle_metres(a: tuple[float, float], b: tuple[float, float]) -> int:
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0]))
         * math.sin(dlon / 2) ** 2)
    return round(6_371_000 * 2 * math.asin(math.sqrt(h)))


def load_long_haul(path: Path, stops: int) -> tuple[list[dict], dict]:
    """The furthest deliveries from a depot: a day with real driving in it.

    Hours-of-service only bites when the driving is long. A dense urban round
    never reaches 4.5 hours at the wheel, so a demo built on one would show a
    rules engine that never fires and prove nothing about it.
    """
    data = json.loads(path.read_text())
    deliveries, depot = data["deliveries"], data["depots"][0]
    home = (depot["latitude"], depot["longitude"])
    ranked = sorted(deliveries, reverse=True,
                    key=lambda d: great_circle_metres(
                        home, (d["latitude"], d["longitude"])))
    return ranked[:stops], depot


def fetch_matrix(depot: dict, deliveries: list[dict],
                 straight_line: bool) -> tuple[list[list], list[list]]:
    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    if straight_line:
        size = len(points)
        distances = [[0] * size for _ in range(size)]
        durations = [[0] * size for _ in range(size)]
        for i in range(size):
            for j in range(size):
                if i != j:
                    metres = great_circle_metres(points[i], points[j])
                    distances[i][j] = metres
                    durations[i][j] = round(metres / 40_000 * 3600)
        return durations, distances

    coordinates = [{"longitude": lon, "latitude": lat} for lat, lon in points]
    response = httpx.post(f"{GATEWAY}/matrix",
                          json={"coordinates": coordinates,
                                "annotations": "duration,distance"},
                          timeout=120)
    if response.status_code != 200:
        raise SystemExit(f"gateway returned {response.status_code}: "
                         f"{response.text[:200]}")
    body = response.json()
    return body["durations"], body["distances"]


def to_problem(depot: dict, deliveries: list[dict], durations: list[list],
               distances: list[list], rules_name: str,
               already_drove: int) -> Problem:
    """Build the day under one rule set, with hours possibly already consumed."""
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for index, delivery in enumerate(deliveries, start=1):
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"],
                                  matrix_index=index))
        orders.append(Order(
            id=delivery["product_id"], kind="JOB",
            quantities={"grams": round(delivery["weight_kg"] * 1000)},
            delivery=StopSpec(location_id=delivery["product_id"],
                              time_windows=(SHIFT,),
                              service_fixed=delivery["service_minutes"] * 60),
        ))

    def grid(raw: list[list]) -> tuple[tuple[int, ...], ...]:
        return tuple(tuple(round(cell) if cell is not None else 10 ** 9
                           for cell in row) for row in raw)

    carry = (DriverState(drive_used=already_drove, duty_used=already_drove)
             if already_drove else None)
    total = sum(o.quantities["grams"] for o in orders)
    vehicle = Vehicle(id="TRUCK-1", capacities={"grams": total}, shift=SHIFT,
                      start_location_id="DEPOT", end_location_id="DEPOT",
                      hos_rules=rules_name, initial_state=carry)
    return Problem(id=f"long-haul-{rules_name}", locations=tuple(locations),
                   orders=tuple(orders), vehicles=(vehicle,),
                   matrix=TravelMatrix(version="matrix-v1",
                                       durations=grid(durations),
                                       distances=grid(distances)))


def show(rules: HoursOfServiceRules, problem: Problem,
         order_ids: list[str]) -> dict:
    """Schedule the day under one rule set and have the verifier judge it."""
    timeline = schedule_route(problem, "TRUCK-1", order_ids, rules)
    solution = Solution(problem_id=problem.id,
                        routes=(Route(vehicle_id="TRUCK-1",
                                      steps=timeline.steps),),
                        unassigned=(), objective_breakdown={},
                        status="FEASIBLE" if timeline.legal else "INFEASIBLE")
    report = verify(problem, solution)
    breaks = [s for s in timeline.steps if s.type == "BREAK"]
    span = timeline.steps[-1].arrival - timeline.steps[0].departure

    print(f"\n  {rules.name}")
    print(f"    driving        {timeline.state.drive_used / HOUR:>6.2f} h "
          f"(limit {rules.max_drive / HOUR:.0f} h)")
    print(f"    duty span      {span / HOUR:>6.2f} h "
          f"(limit {rules.max_duty / HOUR:.0f} h)")
    print(f"    breaks         {len(breaks):>6}"
          + (f"  x {rules.break_duration // 60} min" if breaks else ""))
    for step in breaks:
        print(f"                     {step.rule_ref}  "
              f"{step.placement}  at {step.arrival / HOUR:.2f} h")
    print(f"    scheduler      {'legal' if timeline.legal else 'ILLEGAL'}"
          + (f" -- {timeline.violation}" if timeline.violation else ""))
    verdict = ("accepts" if report.ok else "REJECTS")
    print(f"    verifier       {verdict}"
          + ("" if report.ok else
             " -- " + "; ".join(str(v) for v in report.violations[:2])))
    if "INV-7" in report.not_applicable:
        print("    verifier       INV-7 not applicable (no rule set declared)")
    return {"timeline": timeline, "report": report, "breaks": breaks}


def explain_displacement(problem: Problem, order_ids: list[str]) -> None:
    """Show that breaks push later stops, rather than being appended."""
    print("\n" + "=" * 72)
    print("breaks are inside the evaluation, not bolted on after it")
    print("=" * 72)

    with_hos = schedule_route(problem, "TRUCK-1", order_ids, EU_561)
    without = schedule_route(problem, "TRUCK-1", order_ids, None)
    if not any(s.type == "BREAK" for s in with_hos.steps):
        print("\n  This day is short enough to need no breaks -- try --stops 8+.\n")
        return

    print(f"\n  {'stop':<26}{'no rules':>12}{'EU-561':>12}{'displaced by':>14}")
    print("  " + "-" * 62)
    plain = {s.order_id: s.arrival for s in without.steps if s.order_id}
    for step in with_hos.steps:
        if not step.order_id:
            continue
        before, after = plain[step.order_id], step.arrival
        print(f"  {step.order_id:<26}{before / HOUR:>11.2f}h"
              f"{after / HOUR:>11.2f}h{(after - before) / 60:>13.0f}m")
    print("\n  A post-processing pass would leave the middle column unchanged and")
    print("  append the break at the end -- reporting arrival times that were")
    print("  computed before the break existed. §6.4 names the consequence: a")
    print("  plan that loses its last stops on publication.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # Three of the furthest stops is ~10.5h of driving: past EU-561's 9h day
    # and inside US-HOS's 11h, so the jurisdictions actually split. More stops
    # and both refuse, which demonstrates rejection but not the difference.
    parser.add_argument("--stops", type=int, default=3)
    parser.add_argument("--already-drove", type=float, default=0.0,
                        metavar="HOURS",
                        help="hours the driver has already worked (§6.4 carry-over)")
    parser.add_argument("--straight-line", action="store_true",
                        help="skip the gateway and use great-circle distances")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()

    if not args.dataset.exists():
        raise SystemExit(f"no dataset at {args.dataset}; see docs/dataset_prep.md")

    deliveries, depot = load_long_haul(args.dataset, args.stops)
    home = (depot["latitude"], depot["longitude"])
    furthest = max(great_circle_metres(home, (d["latitude"], d["longitude"]))
                   for d in deliveries)
    print(f"depot {depot['name']} -- {len(deliveries)} of the furthest stops, "
          f"out to {furthest / 1000:,.0f} km")
    if args.already_drove:
        print(f"driver has already worked {args.already_drove:g} h today")
    print("using great-circle distances (--straight-line)" if args.straight_line
           else f"fetching a road matrix from {GATEWAY}")

    durations, distances = fetch_matrix(depot, deliveries, args.straight_line)
    carry = round(args.already_drove * HOUR)

    print("\nthe same day, under each rule set:")
    for rules in (EU_561, US_HOS):
        problem = to_problem(depot, deliveries, durations, distances,
                             rules.name, carry)
        show(rules, problem, [o.id for o in problem.orders])

    eu = to_problem(depot, deliveries, durations, distances, "EU-561", carry)
    explain_displacement(eu, [o.id for o in eu.orders])
    return 0


if __name__ == "__main__":
    sys.exit(main())
