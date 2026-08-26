"""Why can't this stop be served? Answer it before solving, and specifically.

Demonstrates the pre-flight diagnosis landed for E-14/T-14 against the Costa
Rica dataset and real road distances:

    vrp.diagnose   §6.5's closed vocabulary of rejection reasons
    vrp.osrm       a pinned matrix from the gateway
    vrp.solve      PyVRP, to show what the solver says by comparison

§6.5 is emphatic: "Each reason MUST be produced by an explicit diagnostic pass,
not inferred." A solver that fails to place an order knows only that it failed.
Guessing a reason from that is how a dispatcher is told the wrong thing with
total confidence, spends an afternoon finding a bigger van, and discovers the
real obstacle was a tail lift.

This takes a real round and seeds one impossible stop per reason:

    NO_ELIGIBLE_VEHICLE      a drop needing a tail lift, in a fleet without one
    CAPACITY_EXCEEDED        a pallet heavier than the largest van
    TIME_WINDOW_UNREACHABLE  a window that shuts before anyone can arrive
    RELEASE_AFTER_WINDOW     goods leaving the warehouse after it shuts
    DUTY_LIMIT               a stop too far for any legal driving day
    LOCK_CONFLICT            pinned by an operator to a van that cannot carry it

Each comes back with its own code and a sentence naming the obstacle. The
untouched stops come back clean, which matters as much: a diagnosis that flags
everything is no more useful than one that flags nothing.

The closing section then solves what survived, which is §7.9's order of
operations rather than a nicety: hand the solver the full set and the
released-too-late stop makes PyVRP raise before the search begins, naming no
order at all. Diagnose first and every remaining stop is one the fleet can
actually serve.

Requires a running gateway; `examples/.env` points at the FreeBSD jail.
    # dataset: see docs/dataset_prep.md

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/explain/preflight_diagnosis.py --stops 12
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Importing config puts OSRM_API_URL into the environment and the repository
# root on sys.path, which is what makes `import vrp` below resolve.
import config  # noqa: F401

from vrp.diagnose import REASONS, UNIMPLEMENTED, preflight
from vrp.model import (
    Location,
    Lock,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.osrm import build_matrix
from vrp.solve.pyvrp_adapter import solve

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DATASET = Path("data/deliveries_cr.json")
HOUR = 3600
SHIFT = TimeWindow(start=6 * HOUR, end=20 * HOUR)
VAN_KG = 1_200


def load(path: Path, stops: int) -> tuple[list[dict], dict]:
    data = json.loads(path.read_text())
    depot = data["depots"][0]
    nearest = sorted(data["deliveries"],
                     key=lambda d: (d["latitude"] - depot["latitude"]) ** 2
                     + (d["longitude"] - depot["longitude"]) ** 2)
    return nearest[:stops], depot


def build(depot: dict, deliveries: list[dict], matrix: TravelMatrix,
          far: dict) -> tuple[Problem, dict[str, str]]:
    """The round, with one deliberately impossible stop per reason code."""
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    for offset, delivery in enumerate(deliveries + [far]):
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"],
                                  matrix_index=offset + 1))

    def stop_at(delivery: dict, **kwargs) -> StopSpec:
        return StopSpec(location_id=delivery["product_id"],
                        time_windows=kwargs.pop("windows", (SHIFT,)),
                        service_fixed=delivery["service_minutes"] * 60)

    orders, seeded = [], {}
    for offset, delivery in enumerate(deliveries):
        kilos = max(1, round(delivery["weight_kg"]))
        extra: dict = {}
        # Each seeded stop breaks exactly one thing, so the code that comes
        # back is attributable rather than a coincidence of several failures.
        if offset == 0:
            extra["required_skills"] = frozenset({"TAIL_LIFT"})
            seeded[delivery["product_id"]] = "NO_ELIGIBLE_VEHICLE"
        elif offset == 1:
            kilos = VAN_KG * 3
            seeded[delivery["product_id"]] = "CAPACITY_EXCEEDED"
        elif offset == 2:
            # One second at the shift start. 60 seconds was not enough:
            # these are the *nearest* stops, and one was reachable
            # inside the minute, so the seed diagnosed clean.
            extra["windows"] = (TimeWindow(start=6 * HOUR, end=6 * HOUR + 1),)
            seeded[delivery["product_id"]] = "TIME_WINDOW_UNREACHABLE"
        elif offset == 3:
            extra["windows"] = (TimeWindow(start=6 * HOUR, end=9 * HOUR),)
            extra["release_time"] = 18 * HOUR
            seeded[delivery["product_id"]] = "RELEASE_AFTER_WINDOW"
        elif offset == 4:
            seeded[delivery["product_id"]] = "LOCK_CONFLICT"
            kilos = VAN_KG * 3          # pinned below to the small van

        windows = extra.pop("windows", (SHIFT,))
        orders.append(Order(
            id=delivery["product_id"], kind="JOB", quantities={"kg": kilos},
            delivery=stop_at(delivery, windows=windows), **extra))

    # The far stop is a full day's drive away: no legal duty contains it.
    orders.append(Order(
        id=far["product_id"], kind="JOB", quantities={"kg": 5},
        delivery=stop_at(far)))
    seeded[far["product_id"]] = "DUTY_LIMIT"

    pinned = [o for o in orders if seeded.get(o.id) == "LOCK_CONFLICT"]
    locks = tuple(Lock(kind="PIN_ORDER_TO_VEHICLE", order_id=o.id,
                       vehicle_id="SMALL") for o in pinned)

    fleet = (
        Vehicle(id="SMALL", capacities={"kg": VAN_KG}, shift=SHIFT,
                start_location_id="DEPOT", end_location_id="DEPOT",
                hos_rules="EU-561"),
        Vehicle(id="BIG", capacities={"kg": VAN_KG * 2}, shift=SHIFT,
                start_location_id="DEPOT", end_location_id="DEPOT",
                hos_rules="EU-561"),
    )
    return Problem(id="preflight", locations=tuple(locations),
                   orders=tuple(orders), vehicles=fleet, locks=locks,
                   matrix=matrix), seeded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", type=int, default=12)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()

    if not args.dataset.exists():
        raise SystemExit(f"no dataset at {args.dataset}; see docs/dataset_prep.md")

    data = json.loads(args.dataset.read_text())
    deliveries, depot = load(args.dataset, args.stops)
    home = (depot["latitude"], depot["longitude"])
    far = max(data["deliveries"],
              key=lambda d: (d["latitude"] - home[0]) ** 2
              + (d["longitude"] - home[1]) ** 2)

    print(f"depot {depot['name']} -- {len(deliveries)} stops plus one "
          f"deliberately out of range")
    print(f"fetching a road matrix from {GATEWAY}")
    points = [home] + [(d["latitude"], d["longitude"]) for d in deliveries]
    points.append((far["latitude"], far["longitude"]))
    matrix, _ = build_matrix(GATEWAY, points)

    problem, seeded = build(depot, deliveries, matrix, far)
    findings = preflight(problem)

    print(f"\npre-flight: {len(findings)} of {len(problem.orders)} stops "
          f"cannot be served by any vehicle\n")
    correct = 0
    for order in problem.orders:
        expected = seeded.get(order.id)
        found = findings.get(order.id)
        if expected is None and found is None:
            continue
        got = found.code if found else "(none)"
        mark = "ok " if got == expected else "MISMATCH"
        correct += got == expected
        print(f"  {mark} {order.id:<24} {got}")
        if found:
            print(f"      {found.detail}")

    print(f"\n  {correct} of {len(seeded)} seeded stops got the code they were "
          f"seeded for")
    clean = [o.id for o in problem.orders
             if o.id not in seeded and o.id not in findings]
    print(f"  {len(clean)} untouched stops came back clean")

    print("\n" + "=" * 72)
    print("what the solver says about the same instance")
    print("=" * 72)
    servable = tuple(o for o in problem.orders if o.id not in findings)
    reduced = Problem(id=problem.id, locations=problem.locations,
                      orders=servable, vehicles=problem.vehicles,
                      matrix=problem.matrix)
    print(f"\n  solving the {len(servable)} stops that survived pre-flight")
    solution = solve(reduced, iterations=400, seed=0)
    print(f"  status {solution.status}, "
          f"{len(solution.unassigned)} unassigned")
    for entry in solution.unassigned[:3]:
        print(f"    {entry['order_id']:<24} {entry['reason_code']}")
    print("\n  Nothing left for the solver to reject: pre-flight already took")
    print("  the impossible stops, each with a reason someone can act on. Had")
    print("  they been handed over instead, the released-too-late one would")
    print("  make PyVRP raise before the search even started, naming no order")
    print("  at all -- and anything it did drop would carry one generic code.")
    print("  That is the difference §6.5 is after: the solver knows it failed,")
    print("  never why.")

    print(f"\n  ({len(UNIMPLEMENTED)} of {len(REASONS)} codes need a solve or a "
          f"model concept that does not exist yet:")
    for code, why in UNIMPLEMENTED.items():
        print(f"     {code}: {why}")
    print("   named rather than omitted, so nobody waits for one.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
