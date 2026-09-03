"""E03 — What the quality gap costs in compute.

E02 measures how far the gateway's sweep-and-`/trip` plan sits behind the
solver. This measures the other half of that trade: how much search the solver
needs before it passes the gateway, and what it buys after that.

NFR-03 requires anytime behaviour — a usable incumbent at any point, improving
with budget. Nothing in the repository plots it. This does, on the same pinned
matrix and the same instance as E02, so the two results compose.

Writes `results/e03_anytime.json`.
"""

from __future__ import annotations

import argparse
from typing import Any

from common import client, coord, load_deliveries, post, record, sample, timed
from e02_heuristic_vs_solver import SEED, WEIGHTS, build_problem, sequences_from

from vrp.evaluator import evaluate
from vrp.solve.pyvrp_adapter import solve

BUDGETS = (25, 50, 100, 200, 400, 800, 1600, 3200, 6400)


def main() -> None:
    """Solve one instance at a ladder of budgets and locate the crossover."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stops", type=int, default=60)
    parser.add_argument("--capacity", type=int, default=20)
    args = parser.parse_args()

    deliveries, depots, _ = load_deliveries()
    depot = depots[0]
    stops = sample([d for d in deliveries if d.get("gam")], args.stops, SEED)

    with client() as http:
        matrix = post(http, "/matrix", {
            "coordinates": [coord(depot)] + [coord(s) for s in stops],
            "annotations": "duration,distance",
        })
        plan, gateway_ms = timed(post, http, "/vrp", {
            "depots": [{"id": depot["name"], **coord(depot)}],
            "stops": [{"id": s["order_id"], **coord(s)} for s in stops],
            "capacity": args.capacity,
            "roundtrip": True,
        })

    problem = build_problem(depot, stops, matrix["durations"], matrix["distances"],
                            vehicles=len(plan["routes"]), capacity=args.capacity)
    gateway_m = evaluate(problem, {f"VEH-{n + 1}": list(r["stop_ids"])
                                   for n, r in enumerate(plan["routes"])},
                         weights=WEIGHTS).breakdown["distance"]

    ladder: list[dict[str, Any]] = []
    crossover = None
    for budget in BUDGETS:
        solved, ms = timed(solve, problem, iterations=budget, seed=0)
        metres = evaluate(problem, sequences_from(solved),
                          weights=WEIGHTS).breakdown["distance"]
        beats = metres < gateway_m
        ladder.append({"iterations": budget, "distance_m": metres,
                       "wall_ms": round(ms, 1), "beats_gateway": beats,
                       "gap_vs_gateway_pct": 100.0 * (metres - gateway_m) / gateway_m})
        if beats and crossover is None:
            crossover = {"iterations": budget, "wall_ms": round(ms, 1)}
        print(f"  {budget:>5} iters  {metres:>9,} m  {ms:>7.0f} ms  "
              f"{'beats' if beats else 'behind'} gateway")

    best = min(ladder, key=lambda row: row["distance_m"])
    print(record("e03_anytime", {
        "stops": args.stops, "capacity": args.capacity, "seed": SEED,
        "vehicles": len(plan["routes"]),
        "gateway_canonical_m": gateway_m,
        "ladder": ladder,
        "first_budget_beating_gateway": crossover,
        "best": best,
        "gateway_wall_ms": round(gateway_ms, 1),
        "improvement_first_to_best_pct":
            100.0 * (ladder[0]["distance_m"] - best["distance_m"]) / ladder[0]["distance_m"],
        "note": ("gateway_wall_ms is end-to-end and includes its own matrix and "
                 "one /trip round trip per chunk over the network; solver wall_ms "
                 "is search only, on an already-pinned matrix. They are not "
                 "directly comparable and are reported separately for that reason."),
    }))


if __name__ == "__main__":
    main()
