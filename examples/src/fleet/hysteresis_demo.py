"""
Hysteresis Buffer Demo

Shows how the hysteresis mechanism prevents stop assignments from "flapping"
between two nearly-equidistant depots.

The scenario: a stop is placed almost exactly halfway between DEPOT-A and
DEPOT-B. Small road network variations could cause it to flip on every
request. The hysteresis buffer (default 2000m) keeps it anchored to the
closer Euclidean depot unless the other depot is significantly better.

This script runs /vrp/allocate three times:
  1. hysteresis_m=0      — no buffer, assignment may flip
  2. hysteresis_m=2000   — default buffer, stable
  3. hysteresis_m=10000  — large buffer, almost never switches

Usage:
    uv run --package osrm-api-gateway-examples examples/src/fleet/hysteresis_demo.py

Requires:
    - OSRM API Gateway running at http://localhost:8000
"""


import httpx
from config import settings

API_BASE_URL = settings.OSRM_API_URL


def allocate(hysteresis_m: float) -> dict:
    payload = {
        "depots": [
            {"id": "DEPOT-A", "latitude": 9.9300, "longitude": -84.0900},
            {"id": "DEPOT-B", "latitude": 9.9400, "longitude": -84.0800},
        ],
        "stops": [
            # Borderline stop — almost exactly midway between both depots
            {"id": "MID-01", "latitude": 9.9350, "longitude": -84.0850},
            # Clearly closer to DEPOT-A (by Euclidean and likely road)
            {"id": "NEAR-A", "latitude": 9.9310, "longitude": -84.0890},
            # Clearly closer to DEPOT-B (by Euclidean and likely road)
            {"id": "NEAR-B", "latitude": 9.9390, "longitude": -84.0810},
        ],
        "clustering_mode": "travel_time",
        "hysteresis_m": hysteresis_m,
    }
    url = f"{API_BASE_URL}/vrp/allocate"
    resp = httpx.post(url, json=payload, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def print_assignment(result: dict, label: str):
    alloc = result["allocations"]
    print(f"\n--- {label} ---")
    for depot_id in sorted(alloc.keys(), key=lambda x: str(x)):
        stops = alloc[depot_id]
        print(f"  {depot_id}: {', '.join(str(s) for s in stops)}")
    unreachable = result.get("unreachable_stops", [])
    if unreachable:
        print(f"  ❌ Unreachable: {', '.join(str(s) for s in unreachable)}")


def main():
    print("=" * 60)
    print("Hysteresis Demo — Borderline Stop Assignment Stability")
    print("=" * 60)
    print()
    print("Setup:")
    print("  DEPOT-A  at (9.9300, -84.0900)")
    print("  DEPOT-B  at (9.9400, -84.0800)")
    print("  MID-01   at (9.9350, -84.0850)  ← midway between A and B")
    print("  NEAR-A   at (9.9310, -84.0890)  ← clearly near A")
    print("  NEAR-B   at (9.9390, -84.0810)  ← clearly near B")
    print()

    results = {}
    for hyst in [0, 2000, 10000]:
        results[hyst] = allocate(hyst)
        label = f"hysteresis_m = {hyst}  {'(no buffer)' if hyst == 0 else '(default)' if hyst == 2000 else '(aggressive)'}"
        print_assignment(results[hyst], label)

    print()
    print("--- Analysis ---")

    def find_stop(d, stop_id):
        for depot, stops in d.items():
            if stop_id in stops:
                return depot
        return "UNREACHABLE"

    for stop in ["MID-01", "NEAR-A", "NEAR-B"]:
        assignments = [find_stop(results[h]["allocations"], stop) for h in [0, 2000, 10000]]
        is_stable = len(set(assignments)) == 1
        icon = "✅" if is_stable else "⚠️"
        print(f"  {icon} {stop}: h=0→{assignments[0]}, h=2000→{assignments[1]}, h=10000→{assignments[2]}")
        if not is_stable:
            print(f"     → Flapping detected! Different hysteresis values assign {stop} to different depots.")


if __name__ == "__main__":
    main()
