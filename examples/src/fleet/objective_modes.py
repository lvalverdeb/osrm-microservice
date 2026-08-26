"""Is this delivery worth driving to? Ask the objective, not the solver.

Demonstrates the lexicographic objective landed for E-13/T-13 against the Costa
Rica dataset:

    vrp.objective  the tiers, the instance-derived scaling, and §5.2's modes
    vrp.solve      PyVRP, run once per candidate policy
    vrp.verify     the independent verifier, so no illegal plan is compared

A day's stops here are one tight cluster near the depot plus a handful of remote
outliers. The outliers are the interesting part: a 190 km round trip to drop one
parcel costs far more in driving than the parcel is worth. Two plans are built
for the same day -- one that must serve every stop, one that may leave stops
undelivered -- and each objective mode is asked which it prefers.

**They disagree, and that is the feature.** `PRIZE_COLLECTING` takes the plan
that abandons the outlier; every other mode refuses, because for them no
quantity of distance buys an undelivered order. SDD §5.2 puts it as Tier 2
sharing a level with cost in that mode alone.

A flat weighted sum cannot express the difference. One set of weights gives one
answer, and changing the answer means retuning weights until the output looks
right -- which §5.1 calls "the most common modelling error in production
routing", because those weights then silently invert on a larger day.

One honest limitation, worth knowing before reading the output: `MIN_COST` and
`MIN_VEHICLES` always agree here. In this model more vehicles is monotonically
worse -- a single-vehicle tour is a lower bound on distance, and every extra van
adds its own depot legs -- so a van can never repay its fixed cost. The trade
§5.2 describes for `MIN_COST` needs **overtime** in Tier 4 (`T-25`, hours of
service), which is not implemented. Until it is, the two modes coincide on real
data, and this example says so rather than staging a disagreement.

Requires a running gateway with an engine behind it, for real road distances:
    make compose-up                      # note: compose publishes on :8080
    # dataset: see docs/dataset_prep.md

`examples/.env` already points at the FreeBSD jail, so no override is needed for
the usual case. Override `OSRM_API_URL` to reach the Docker path instead, which
publishes 8080 rather than 8000. Either way the host is not localhost: the
Docker daemon here is remote (`DOCKER_HOST`), so published ports live on that
VM while `docker ps` still prints `0.0.0.0:8080->8000/tcp`.

Without a gateway, pass --straight-line to substitute great-circle distances.
The comparison still holds; the numbers are no longer road numbers. Measured on
a 20-stop slice with 2 outliers, the two agree on every mode and differ only in
magnitude: 572,007 m saved by road against 381,299 m straight-line.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/objective_modes.py --stops 20 --outliers 2
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

from vrp.model import (
    UNREACHABLE,
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)
from vrp.objective import Mode, ObjectiveSpec, Tier, compare, score
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

GATEWAY = os.environ.get("OSRM_API_URL", "http://localhost:8000")
DATASET = Path("data/deliveries_cr.json")

SHIFT = TimeWindow(start=0, end=24 * 3600)

# What one parcel is worth, against one metre driven. Both integers in the same
# currency: §5.2's PRIZE_COLLECTING compares them directly, so the ratio between
# these two numbers is the whole decision.
PARCEL_PRIZE = 5_000
COST_PER_METRE = 1
VAN_FIXED_COST = 50_000

# Orders are priority tier 1, not 0. Tier 0 is §5.1's never-droppable class --
# leaving it at the model default would put every order in Tier 1 of the
# objective, where no prize applies and nothing can ever be dropped.
DROPPABLE_TIER = 1


def great_circle_metres(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Straight-line metres. Only used by --straight-line; see the docstring."""
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0]))
         * math.sin(dlon / 2) ** 2)
    return round(6_371_000 * 2 * math.asin(math.sqrt(h)))


def load_slice(path: Path, stops: int, outliers: int,
               province: str | None) -> tuple[list[dict], dict]:
    """A tight cluster near one depot, plus the furthest few deliveries.

    The shape is the point. A uniformly tight day has no decision in it: every
    stop is worth serving and every mode agrees. The outliers are what make the
    objective's mode matter.
    """
    data = json.loads(path.read_text())
    deliveries = data["deliveries"]
    if province:
        deliveries = [d for d in deliveries if d["province"] == province]
        if not deliveries:
            raise SystemExit(f"no deliveries in province {province!r}")

    depot = min(
        data["depots"],
        key=lambda w: sum((d["latitude"] - w["latitude"]) ** 2
                          + (d["longitude"] - w["longitude"]) ** 2
                          for d in deliveries[:400]),
    )
    home = (depot["latitude"], depot["longitude"])
    ranked = sorted(deliveries,
                    key=lambda d: great_circle_metres(
                        home, (d["latitude"], d["longitude"])))
    near = ranked[:max(stops - outliers, 1)]
    far = ranked[-outliers:] if outliers else []
    return near + far, depot


def fetch_matrix(depot: dict, deliveries: list[dict]) -> tuple[list[list], list[list]]:
    """Real road durations and distances, from the gateway's /matrix."""
    coordinates = [{"longitude": depot["longitude"], "latitude": depot["latitude"]}]
    coordinates += [{"longitude": d["longitude"], "latitude": d["latitude"]}
                    for d in deliveries]
    response = httpx.post(f"{GATEWAY}/matrix",
                          json={"coordinates": coordinates,
                                "annotations": "duration,distance"},
                          timeout=120)
    if response.status_code != 200:
        raise SystemExit(f"gateway returned {response.status_code}: "
                         f"{response.text[:200]}")
    body = response.json()
    return body["durations"], body["distances"]


def straight_line_matrix(depot: dict,
                         deliveries: list[dict]) -> tuple[list[list], list[list]]:
    """Great-circle stand-in for the gateway, at 40 km/h."""
    points = [(depot["latitude"], depot["longitude"])]
    points += [(d["latitude"], d["longitude"]) for d in deliveries]
    size = len(points)
    distances = [[0] * size for _ in range(size)]
    durations = [[0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i == j:
                continue
            metres = great_circle_metres(points[i], points[j])
            distances[i][j] = metres
            durations[i][j] = round(metres / 40_000 * 3600)
    return durations, distances


def to_problem(depot: dict, deliveries: list[dict], durations: list[list],
               distances: list[list], *, droppable: bool) -> Problem:
    """Build the day, keeping `product_id` as the order id.

    `droppable` is the whole experiment. The adapter treats an order as required
    exactly when its prize is zero, so a prize-bearing order is one the solver is
    allowed to leave undelivered -- and a prizeless one is a promise it must keep.
    """
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for index, delivery in enumerate(deliveries, start=1):
        locations.append(Location(id=delivery["product_id"],
                                  lat=delivery["latitude"],
                                  lon=delivery["longitude"],
                                  matrix_index=index))
        orders.append(Order(
            id=delivery["product_id"],
            kind="JOB",
            quantities={"grams": round(delivery["weight_kg"] * 1000)},
            priority_tier=DROPPABLE_TIER,
            prize=PARCEL_PRIZE if droppable else 0,
            delivery=StopSpec(location_id=delivery["product_id"],
                              time_windows=(SHIFT,),
                              service_fixed=delivery["service_minutes"] * 60),
        ))

    # OSRM returns nulls for unreachable pairs and floats throughout; the model
    # takes whole seconds and metres.
    def grid(raw: list[list]) -> tuple[tuple[int, ...], ...]:
        # MTX-5: a null cell is unreachable, and must stay distinguishable.
        # This said `10 ** 9` before E-10 -- a large finite arc a solver will
        # happily optimise into a plan, returning a leg nobody can drive.
        return tuple(tuple(round(cell) if cell is not None else UNREACHABLE
                           for cell in row) for row in raw)

    total_grams = sum(o.quantities["grams"] for o in orders)
    vehicle = Vehicle(id="VAN-1", capacities={"grams": total_grams}, shift=SHIFT,
                      start_location_id="DEPOT", end_location_id="DEPOT")
    return Problem(id="cr-objective-demo", locations=tuple(locations),
                   orders=tuple(orders), vehicles=(vehicle,),
                   matrix=TravelMatrix(version="matrix-v1",
                                       durations=grid(durations),
                                       distances=grid(distances)))


def build_candidate(depot: dict, deliveries: list[dict], durations: list[list],
                    distances: list[list], *, droppable: bool,
                    iterations: int) -> dict:
    """Solve one policy and have the independent verifier judge the plan.

    A plan that does not verify is discarded rather than reported. CON-1 puts
    feasibility above optimality, so an illegal plan is not a cheap option -- it
    is not an option, and comparing its cost against a legal one is exactly the
    mistake the benchmark gate exists to prevent.
    """
    problem = to_problem(depot, deliveries, durations, distances,
                         droppable=droppable)
    solution = solve(problem, iterations=iterations, seed=0)
    report = verify(problem, solution)
    if not report.ok:
        raise SystemExit("plan failed verification: "
                         + "; ".join(str(v) for v in report.violations[:2]))
    spec = ObjectiveSpec(mode=Mode.MIN_COST, vehicle_fixed_cost=VAN_FIXED_COST,
                         cost_per_metre=COST_PER_METRE)
    return {
        "label": "may drop stops" if droppable else "must serve all",
        "problem": problem,
        "solution": solution,
        "values": score(problem, solution, spec).values,
        "dropped": [u["order_id"] for u in solution.unassigned],
    }


def report(candidates: list[dict]) -> None:
    """Score both plans under every mode and show which each prefers."""
    print("\ntwo plans for the same day\n")
    header = f"  {'policy':<16}{'distance':>13}{'undelivered':>13}{'prize forgone':>15}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for entry in candidates:
        print(f"  {entry['label']:<16}"
              f"{entry['values'][Tier.OPERATING]:>13,}"
              f"{len(entry['dropped']):>13}"
              f"{entry['values'][Tier.UNSERVED]:>15,}")

    print("\nwhich plan each mode prefers:\n")
    for mode in Mode:
        spec = ObjectiveSpec(mode=mode, vehicle_fixed_cost=VAN_FIXED_COST,
                             cost_per_metre=COST_PER_METRE)
        best = candidates[0]
        for entry in candidates[1:]:
            if compare(entry["values"], best["values"], spec) < 0:
                best = entry
        levels = " > ".join("+".join(t.name for t in g) for g in spec.levels())
        print(f"  {mode.name:<17} {best['label']}")
        print(f"  {'':<17} {levels}\n")


def explain(candidates: list[dict]) -> None:
    """The arithmetic a weighted sum would have hidden."""
    print("=" * 74)
    print("why the modes disagree")
    print("=" * 74)

    serve = next(c for c in candidates if not c["dropped"])
    drop = next((c for c in candidates if c["dropped"]), None)
    if drop is None:
        print("\n  The solver kept every stop even when allowed to drop them, so\n"
              "  there is no disagreement to show on this slice. Try more\n"
              "  --outliers, or a smaller PARCEL_PRIZE.\n")
        return

    saved = serve["values"][Tier.OPERATING] - drop["values"][Tier.OPERATING]
    forgone = drop["values"][Tier.UNSERVED]
    print(f"\n  Dropping {len(drop['dropped'])} stop(s) saves {saved:,} metres "
          f"of driving\n  and forgoes {forgone:,} of prize.\n")
    for order_id in drop["dropped"]:
        print(f"    abandoned: {order_id}")

    verdict = "worth it" if saved > forgone else "not worth it"
    print(f"\n  In one currency that is {verdict}: {saved:,} saved against "
          f"{forgone:,} forgone.")
    print("  PRIZE_COLLECTING is the only mode allowed to make that trade --")
    print("  §5.2 puts Tier 2 on the same level as cost for it alone. Every")
    print("  other mode keeps Tier 2 strictly above cost, so it serves the")
    print("  stop however far away it is.\n")
    print("  Same plans, same numbers, opposite answers, decided by the mode")
    print("  rather than by tuning weights until the output looked right.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stops", type=int, default=20)
    parser.add_argument("--outliers", type=int, default=2,
                        help="how many of the furthest deliveries to include")
    parser.add_argument("--province", default=None,
                        help="restrict to one province, e.g. 'San Jose'")
    parser.add_argument("--iterations", type=int, default=600,
                        help="PyVRP budget per candidate")
    parser.add_argument("--straight-line", action="store_true",
                        help="skip the gateway and use great-circle distances")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()

    if not args.dataset.exists():
        raise SystemExit(f"no dataset at {args.dataset}; see docs/dataset_prep.md")

    deliveries, depot = load_slice(args.dataset, args.stops, args.outliers,
                                   args.province)
    home = (depot["latitude"], depot["longitude"])
    furthest = max(great_circle_metres(home, (d["latitude"], d["longitude"]))
                   for d in deliveries)
    print(f"depot {depot['name']} -- {len(deliveries)} stops"
          f"{f' in {args.province}' if args.province else ''}, "
          f"furthest {furthest / 1000:,.0f} km out")

    if args.straight_line:
        print("using great-circle distances (--straight-line): not road numbers")
        durations, distances = straight_line_matrix(depot, deliveries)
    else:
        print(f"fetching a {len(deliveries) + 1}x{len(deliveries) + 1} road "
              f"matrix from {GATEWAY}")
        durations, distances = fetch_matrix(depot, deliveries)

    print(f"\nsolving twice ({args.iterations} iterations each)")
    candidates = [
        build_candidate(depot, deliveries, durations, distances,
                        droppable=False, iterations=args.iterations),
        build_candidate(depot, deliveries, durations, distances,
                        droppable=True, iterations=args.iterations),
    ]
    report(candidates)
    explain(candidates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
