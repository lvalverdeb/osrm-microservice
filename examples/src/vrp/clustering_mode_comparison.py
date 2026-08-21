"""
VRP Clustering Mode Comparison

Compares the three available clustering modes on the same dataset:
  - travel_time: shortest driving duration (default)
  - distance:    shortest road distance
  - radial:      Euclidean distance only (no road data needed)

Each mode produces different depot-to-stop assignments. This script runs
/vrp/allocate with each mode on a single dataset with explicit stop IDs.

Usage:
    uv run examples/src/vrp/clustering_mode_comparison.py

Requires:
    - OSRM API Gateway running at http://localhost:8000
"""

import os
import sys

import httpx

API_BASE_URL = os.environ.get("OSRM_API_URL", "http://localhost:8000")


def allocate(mode: str, hysteresis_m: float = 2000) -> dict:
    """Run /vrp/allocate and return the allocation response."""
    payload = {
        "depots": [
            {"id": "NORTE",  "latitude": 10.0734, "longitude": -84.3121},  # Grecia (north-west)
            {"id": "CENTRO", "latitude": 9.9333,  "longitude": -84.0833},  # San José (central)
            {"id": "SUR",    "latitude": 9.3734,  "longitude": -83.7029},  # Pérez Zeledón (south)
        ],
        "stops": [
            # Near CENTRO (expected: CENTRO in all modes)
            {"id": "SJ-01", "latitude": 9.9472, "longitude": -84.0531},
            {"id": "SJ-02", "latitude": 9.9281, "longitude": -84.0907},
            {"id": "SJ-03", "latitude": 9.9350, "longitude": -84.0700},
            # Borderline between NORTE and CENTRO (hysteresis matters)
            {"id": "BL-01", "latitude": 10.0000, "longitude": -84.2000},
            {"id": "BL-02", "latitude": 9.9800,  "longitude": -84.1500},
            # Near SUR (expected: SUR in travel_time/distance, radial may differ)
            {"id": "SZ-01", "latitude": 9.4500,  "longitude": -83.7500},
            {"id": "SZ-02", "latitude": 9.4000,  "longitude": -83.6500},
            # Edge case: very far (Puntarenas, far west)
            {"id": "PT-01", "latitude": 9.9763,  "longitude": -84.8384},
        ],
        "clustering_mode": mode,
        "hysteresis_m": hysteresis_m,
    }
    url = f"{API_BASE_URL}/vrp/allocate"
    print(f"  Mode={mode:<12} hysteresis={hysteresis_m}m ...", end=" ")
    sys.stdout.flush()
    resp = httpx.post(url, json=payload, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()
    print(f"OK  ({len(data.get('allocations', {}))} depots, "
          f"{len(data.get('unreachable_stops', []))} unreachable)")
    return data


def print_table(results: dict):
    """Print a stop→depot assignment matrix for all modes."""
    modes = list(results.keys())

    print(f"\n{'Stop ID':<10}", end="")
    for m in modes:
        print(f"  {m:<14}", end="")
    print()
    print("-" * (10 + 16 * len(modes)))

    all_stops = set()
    for r in results.values():
        for stops in r["allocations"].values():
            all_stops.update(stops)
        all_stops.update(r.get("unreachable_stops", []))

    for stop in sorted(str(s) for s in all_stops):
        print(f"{stop:<10}", end="")
        for m in modes:
            assigned = None
            for depot_id, stop_ids in results[m]["allocations"].items():
                if stop in stop_ids:
                    assigned = depot_id
                    break
            if stop in results[m].get("unreachable_stops", []):
                print(f"  {'❌ UNREACHABLE':<14}", end="")
            elif assigned:
                print(f"  {assigned!s:<14}", end="")
            else:
                print(f"  {'—':<14}", end="")
        print()
    print()


def main():
    modes = ["travel_time", "distance", "radial"]
    results = {}

    for mode in modes:
        results[mode] = allocate(mode)

    print("\n=== ALLOCATION COMPARISON ===")
    print_table(results)

    print("=== Hysteresis effect (travel_time mode) ===")
    allocate("travel_time", hysteresis_m=10000)
    allocate("travel_time", hysteresis_m=100)

    print("\nWith  high hysteresis (10km): borderline stops assigned to anchor")
    print("With  low  hysteresis (100m): borderline stops may switch to better depot")


if __name__ == "__main__":
    main()
