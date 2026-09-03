"""E02 — The gateway's `/vrp` heuristic against the platform's solver.

The repository ships two routing paths and has never compared them, because
nothing connects them: the Rust gateway's `/vrp` is sweep-angle allocation plus
one OSRM `/trip` per chunk, and `vrp/` is a solver portfolio behind a canonical
objective. They have no shared scale, so "which is better" has had no answer.

This puts both on one scale by pinning a single matrix and scoring every plan
with `vrp.evaluator`, exactly as `vrp.portfolio` does for competing engines —
an engine's own accounting is never evidence about that engine (INV-9, and §7.3
one level up).

Three things get measured:

* **Agreement.** The gateway reports `total_distance`. Recomputing it from the
  gateway's own sequences on the pinned matrix says whether the two
  implementations agree about what a plan costs, which is INV-9 across a
  language boundary.
* **The sequencing gap**, one vehicle. OSRM `/trip` against the solver on the
  same stops. This isolates ordering quality.
* **The partition gap**, several vehicles. The gateway fixes each vehicle's
  load by sweep angle *before* sequencing; the solver chooses assignment and
  order together. This isolates what the sweep costs.

Writes `results/e02_heuristic_vs_solver.json`.
"""

from __future__ import annotations

import argparse
from typing import Any

from common import client, coord, post, record, sample

from vrp.evaluator import ObjectiveWeights, evaluate
from vrp.model import (
    UNREACHABLE,
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
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

SHIFT = TimeWindow(start=0, end=24 * 3600)
WEIGHTS = ObjectiveWeights(per_metre=1, per_second=0, per_vehicle=0)
SEED = 20260902


def _grid(raw: list[list], scale: float = 1.0) -> tuple[tuple[int, ...], ...]:
    """Round an OSRM grid to integers, keeping nulls distinguishable (MTX-5)."""
    return tuple(tuple(round(cell * scale) if cell is not None else UNREACHABLE
                       for cell in row) for row in raw)


def build_problem(depot: dict[str, Any], stops: list[dict[str, Any]],
                  durations: list[list], distances: list[list],
                  vehicles: int, capacity: int) -> Problem:
    """State the same instance the gateway was given, in the domain model.

    Capacity is counted in stops rather than weight, because that is what the
    gateway's `capacity` field means — the two must express the same limit or
    the comparison is between different problems.
    """
    locations = [Location(id="DEPOT", lat=depot["latitude"],
                          lon=depot["longitude"], matrix_index=0)]
    orders = []
    for index, stop in enumerate(stops, start=1):
        locations.append(Location(id=stop["order_id"], lat=stop["latitude"],
                                  lon=stop["longitude"], matrix_index=index))
        orders.append(Order(id=stop["order_id"], kind="JOB",
                            quantities={"stops": 1},
                            delivery=StopSpec(location_id=stop["order_id"],
                                              time_windows=(SHIFT,),
                                              service_fixed=0)))
    fleet = tuple(Vehicle(id=f"VEH-{n + 1}", capacities={"stops": capacity},
                          shift=SHIFT, start_location_id="DEPOT",
                          end_location_id="DEPOT") for n in range(vehicles))
    return Problem(id="e02", locations=tuple(locations), orders=tuple(orders),
                   vehicles=fleet,
                   matrix=TravelMatrix(version="osrm-live",
                                       durations=_grid(durations),
                                       distances=_grid(distances)))


def sequences_from(solution) -> dict[str, list[str]]:
    """Extract each vehicle's order sequence from a solved `Solution`."""
    out: dict[str, list[str]] = {}
    for route in solution.routes:
        out[route.vehicle_id] = [step.location_id for step in route.steps
                                 if step.location_id != "DEPOT"]
    return out


def as_solution(problem: Problem, score) -> Solution:
    """Wrap an evaluation's timelines as a `Solution` the verifier can judge."""
    return Solution(
        problem_id=problem.id,
        routes=tuple(Route(vehicle_id=vehicle, steps=timeline)
                     for vehicle, timeline in score.timelines.items()),
        objective_breakdown={"distance": score.breakdown["distance"]},
    )


def run(stops_wanted: int, capacity: int, iterations: int) -> dict[str, Any]:
    """Measure one instance at one capacity, returning the comparison."""
    from common import load_deliveries
    deliveries, depots, _ = load_deliveries()
    depot = depots[0]
    gam = [d for d in deliveries if d.get("gam")]
    stops = sample(gam, stops_wanted, SEED)

    with client() as http:
        matrix = post(http, "/matrix", {
            "coordinates": [coord(depot)] + [coord(s) for s in stops],
            "annotations": "duration,distance",
        })
        plan = post(http, "/vrp", {
            "depots": [{"id": depot["name"], **coord(depot)}],
            "stops": [{"id": s["order_id"], **coord(s)} for s in stops],
            "capacity": capacity,
            "roundtrip": True,
        })

    gateway_routes = plan["routes"]
    problem = build_problem(depot, stops, matrix["durations"], matrix["distances"],
                            vehicles=len(gateway_routes), capacity=capacity)

    gateway_assignment = {f"VEH-{n + 1}": list(route["stop_ids"])
                          for n, route in enumerate(gateway_routes)}
    gateway_score = evaluate(problem, gateway_assignment, weights=WEIGHTS)

    solved = solve(problem, iterations=iterations, seed=0)
    solver_assignment = sequences_from(solved)
    solver_score = evaluate(problem, solver_assignment, weights=WEIGHTS)

    gateway_m = gateway_score.breakdown["distance"]
    solver_m = solver_score.breakdown["distance"]
    return {
        "stops": len(stops),
        "capacity": capacity,
        "iterations": iterations,
        "vehicles_gateway": len(gateway_routes),
        "vehicles_solver": sum(1 for s in solver_assignment.values() if s),
        "gateway_reported_m": plan["total_distance"],
        "gateway_canonical_m": gateway_m,
        "agreement_delta_m": plan["total_distance"] - gateway_m,
        "solver_canonical_m": solver_m,
        "gap_pct": 100.0 * (gateway_m - solver_m) / solver_m if solver_m else None,
        "gateway_plan_verified": verify(problem, as_solution(problem, gateway_score)).ok,
        "solver_plan_verified": verify(problem, solved).ok,
        "orders_assigned_solver": sum(len(s) for s in solver_assignment.values()),
    }


def main() -> None:
    """Run the sequencing case and the partition cases."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stops", type=int, default=60)
    parser.add_argument("--iterations", type=int, default=2000)
    args = parser.parse_args()

    cases = {
        "one_vehicle": run(args.stops, args.stops, args.iterations),
        "three_vehicles": run(args.stops, max(1, args.stops // 3), args.iterations),
        "six_vehicles": run(args.stops, max(1, args.stops // 6), args.iterations),
    }
    print(record("e02_heuristic_vs_solver", {"cases": cases, "seed": SEED}))
    for name, case in cases.items():
        print(f"{name:>16}  gateway {case['gateway_canonical_m']:>9,} m   "
              f"solver {case['solver_canonical_m']:>9,} m   "
              f"gap {case['gap_pct']:+.1f}%   "
              f"agreement delta {case['agreement_delta_m']:+.1f} m")


if __name__ == "__main__":
    main()
