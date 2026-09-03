"""E05 — What the hysteresis band actually does, and what it costs.

`select_depot` in `gateway/src/vrp/allocate.rs` anchors each stop to its
**Euclidean**-nearest depot and leaves that anchor only when some other depot's
road cost beats it by more than `hysteresis_m`. It is not a comparison against a
previous run — there is no previous assignment in the request — so the band is a
standing preference for stable geometry over the matrix's current opinion.

Whether it bites therefore depends entirely on **how far apart the depots are**,
which no document says. Two configurations are swept:

* the corpus's six national depots, tens of kilometres apart; and
* four synthetic depots inside the GAM, 8-25 km apart, which is what an urban
  multi-hub operation looks like.

Each sweep reports how many stops the band holds at their straight-line anchor
and what holding them costs in extra depot-to-stop road distance. `radial` mode
is the limit case: it never consults the matrix, so it is an infinite band.

Writes `results/e05_hysteresis.json`.
"""

from __future__ import annotations

import argparse
from typing import Any

import httpx
from common import client, coord, load_deliveries, post, record, sample

SEED = 20260902
BANDS = (0.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)

URBAN_DEPOTS = [
    {"name": "San Jose Centro", "latitude": 9.9333, "longitude": -84.0800},
    {"name": "Heredia", "latitude": 9.9981, "longitude": -84.1197},
    {"name": "Alajuela", "latitude": 10.0162, "longitude": -84.2117},
    {"name": "Cartago", "latitude": 9.8644, "longitude": -83.9194},
]


def allocation_map(plan: dict[str, Any], order: list[str]) -> dict[str, int]:
    """Map each stop id to the index of the depot that took it.

    Args:
        plan: A `/vrp/allocate` response, whose `allocations` is keyed by depot id.
        order: Depot ids in the order they were sent, which fixes the indices.

    Returns:
        Stop id to depot index.
    """
    index_of = {depot_id: n for n, depot_id in enumerate(order)}
    return {stop_id: index_of[depot_id]
            for depot_id, stop_ids in plan["allocations"].items()
            for stop_id in stop_ids}


def depot_to_stop_distances(http: httpx.Client, depots: list[dict[str, Any]],
                            stops: list[dict[str, Any]]) -> list[list[float]]:
    """Road distance from every depot to every stop, as a depot-major grid."""
    body = post(http, "/matrix", {
        "coordinates": [coord(d) for d in depots] + [coord(s) for s in stops],
        "sources": list(range(len(depots))),
        "destinations": list(range(len(depots), len(depots) + len(stops))),
        "annotations": "distance",
    })
    return body["distances"]


def cost_of(assigned: dict[str, int], baseline: dict[str, int],
            stops: list[dict[str, Any]], distances: list[list[float]]) -> float:
    """Extra depot-to-stop road metres `assigned` spends against `baseline`."""
    total = 0.0
    for index, stop in enumerate(stops):
        sid = stop["order_id"]
        if sid in assigned and sid in baseline:
            total += distances[assigned[sid]][index] - distances[baseline[sid]][index]
    return total


def sweep(http: httpx.Client, depots: list[dict[str, Any]],
          stops: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """Sweep every band for one depot configuration."""
    spec = [{"id": d["name"], **coord(d)} for d in depots]
    ids = [d["name"] for d in depots]
    stop_spec = [{"id": s["order_id"], **coord(s)} for s in stops]
    distances = depot_to_stop_distances(http, depots, stops)

    print(f"\n  {label} — {len(depots)} depots, {len(stops)} stops")
    rows: list[dict[str, Any]] = []
    baseline: dict[str, int] = {}
    for band in BANDS:
        plan = post(http, "/vrp/allocate", {
            "depots": spec, "stops": stop_spec,
            "clustering_mode": "distance", "hysteresis_m": band,
        })
        assigned = allocation_map(plan, ids)
        if band == 0.0:
            baseline = assigned
        held = [s for s in assigned if assigned[s] != baseline.get(s)]
        extra = cost_of(assigned, baseline, stops, distances)
        rows.append({"hysteresis_m": band, "held_at_anchor": len(held),
                     "held_pct": round(100.0 * len(held) / len(stops), 2),
                     "extra_road_distance_m": round(extra),
                     "extra_per_held_stop_m": round(extra / len(held)) if held else 0})
        print(f"    band {band:>8.0f} m   held {len(held):>4} / {len(stops)}   "
              f"extra {extra / 1000:>8.1f} km")

    radial = allocation_map(post(http, "/vrp/allocate", {
        "depots": spec, "stops": stop_spec, "clustering_mode": "radial"}), ids)
    radial_held = [s for s in radial if radial[s] != baseline.get(s)]
    radial_extra = cost_of(radial, baseline, stops, distances)
    print(f"    radial (no matrix)   held {len(radial_held):>4} / {len(stops)}   "
          f"extra {radial_extra / 1000:>8.1f} km")

    return {"depots": len(depots), "stops": len(stops), "bands": rows,
            "radial": {"held_at_anchor": len(radial_held),
                       "extra_road_distance_m": round(radial_extra)}}


def main() -> None:
    """Sweep both depot configurations over the same stops."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stops", type=int, default=400)
    args = parser.parse_args()

    deliveries, national, _ = load_deliveries()
    gam = [d for d in deliveries if d.get("gam")]

    with client(timeout=300.0) as http:
        result = {
            "national": sweep(http, national,
                              sample(deliveries, args.stops, SEED), "national"),
            "urban": sweep(http, URBAN_DEPOTS,
                           sample(gam, args.stops, SEED + 3), "urban (GAM)"),
        }

    print(record("e05_hysteresis", {"seed": SEED, "mode": "distance", **result}))


if __name__ == "__main__":
    main()
