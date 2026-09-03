"""E04 — What the gateway costs as instances grow, and what the cache returns.

Three questions paper 02 asserts answers to without measuring them:

* how `/vrp` latency scales with stop count, and how many vehicles falls out;
* how `/matrix` scales toward `MATRIX_MAX_CELLS`, which bounds every solve that
  needs a pinned matrix; and
* what the two cache tiers are actually worth, measured as the same request
  issued twice.

The cache figure is a *warm-vs-cold* measurement on one process, not a hit-rate
over a workload. It says what a hit saves, not how often you get one.

Writes `results/e04_scaling.json`.
"""

from __future__ import annotations

from typing import Any

from common import client, coord, load_deliveries, post, record, sample, timed

SEED = 20260902
VRP_SIZES = (50, 100, 250, 500, 1000, 2000)
MATRIX_SIDES = (10, 25, 50, 75, 100)


def _vrp_payload(depot: dict, stops: list[dict], capacity: int) -> dict[str, Any]:
    return {
        "depots": [{"id": depot["name"], **coord(depot)}],
        "stops": [{"id": s["order_id"], **coord(s)} for s in stops],
        "capacity": capacity,
        "roundtrip": True,
    }


def main() -> None:
    """Walk both ladders, then measure a cold and warm call on one payload."""
    deliveries, depots, _ = load_deliveries()
    depot = depots[0]
    gam = [d for d in deliveries if d.get("gam")]

    vrp_rows: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, Any]] = []

    with client(timeout=300.0) as http:
        for n in VRP_SIZES:
            stops = sample(gam, n, SEED)
            plan, ms = timed(post, http, "/vrp", _vrp_payload(depot, stops, 35))
            vrp_rows.append({
                "stops": n,
                "wall_ms": round(ms, 1),
                "ms_per_stop": round(ms / n, 3),
                "vehicles": len(plan["routes"]),
                "total_distance_m": plan["total_distance"],
                "total_duration_s": plan["total_duration"],
            })
            print(f"  /vrp {n:>5} stops  {ms:>8.0f} ms  {len(plan['routes']):>3} vehicles")

        for side in MATRIX_SIDES:
            rows = sample(gam, side, SEED + 7)
            _, ms = timed(post, http, "/matrix", {
                "coordinates": [coord(r) for r in rows],
                "annotations": "duration,distance",
            })
            matrix_rows.append({"side": side, "cells": side * side,
                                "wall_ms": round(ms, 1)})
            print(f"  /matrix {side:>3}x{side:<3} = {side * side:>6} cells  {ms:>8.0f} ms")

        # Cache: a payload no earlier call in this run used, issued three times.
        fresh = sample(gam, 40, SEED + 99)
        payload = {"coordinates": [coord(r) for r in fresh],
                   "annotations": "duration,distance"}
        _, cold = timed(post, http, "/matrix", payload)
        _, warm1 = timed(post, http, "/matrix", payload)
        _, warm2 = timed(post, http, "/matrix", payload)

    cache = {"cold_ms": round(cold, 1), "warm_ms": [round(warm1, 1), round(warm2, 1)],
             "speedup": round(cold / max(warm1, warm2, 0.001), 2)}
    print(f"  cache  cold {cold:.1f} ms   warm {warm1:.1f}/{warm2:.1f} ms   "
          f"{cache['speedup']}x")

    print(record("e04_scaling", {"seed": SEED, "vrp": vrp_rows,
                                 "matrix": matrix_rows, "cache": cache,
                                 "vrp_capacity": 35}))


if __name__ == "__main__":
    main()
