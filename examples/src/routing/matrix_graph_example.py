"""
Matrix-to-Graph Conversion (/matrix-graph)

Fetches a distance/duration matrix for 5 Costa Rican cities and converts it
into a directed NetworkX graph. Prints node metadata and edge attributes
(duration in seconds, distance in meters).

Usage:
    uv run --package osrm-api-gateway-examples examples/src/routing/matrix_graph_example.py

Requires:
    - OSRM API Gateway running at http://localhost:8000
"""

from typing import Any

import httpx
from config import settings

API_BASE_URL = settings.OSRM_API_URL


def fetch_matrix_graph(coordinates: list[dict[str, float]]) -> dict[str, Any]:
    """Fetch the distance/duration matrix and convert to a graph."""
    payload = {"coordinates": coordinates}
    url = f"{API_BASE_URL}/matrix-graph"
    print(f"POST {url}  ({len(coordinates)} nodes)")
    resp = httpx.post(url, json=payload, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def main():
    cities = [
        {"id": "San José",    "latitude": 9.9333, "longitude": -84.0833},
        {"id": "Alajuela",    "latitude": 10.0167, "longitude": -84.2167},
        {"id": "Heredia",     "latitude": 9.9981, "longitude": -84.1197},
        {"id": "Cartago",     "latitude": 9.8644, "longitude": -83.9194},
        {"id": "Puntarenas",  "latitude": 9.9763, "longitude": -84.8384},
    ]

    graph = fetch_matrix_graph([
        {"latitude": c["latitude"], "longitude": c["longitude"]}
        for c in cities
    ])

    print(f"\nGraph has {len(graph['nodes'])} nodes and {len(graph['edges'])} edges\n")

    print("--- NODES ---")
    for node in graph["nodes"]:
        c = cities[node["id"]]
        print(f"  [{node['id']}] {c['id']}  (lat={node['lat']:.4f}, lon={node['lon']:.4f})")

    print("\n--- EDGES (non-zero) ---")
    print(f"  {'From':<12} {'To':<12} {'Duration (min)':<16} {'Distance (km)':<16}")
    print(f"  {'-'*11} {'-'*11} {'-'*15} {'-'*15}")
    for edge in graph["edges"]:
        if edge["duration"] == 0:
            continue
        from_city = cities[edge["source"]]["id"]
        to_city = cities[edge["target"]]["id"]
        dur_min = edge["duration"] / 60
        dist_km = edge["distance"] / 1000
        print(f"  {from_city:<12} {to_city:<12} {dur_min:<16.1f} {dist_km:<16.2f}")

    total_dur = sum(e["duration"] for e in graph["edges"] if e["duration"] > 0)
    total_dist = sum(e["distance"] for e in graph["edges"] if e["distance"] > 0)
    print(f"\nTotal graph: {total_dur/60:.0f} min of edges, {total_dist/1000:.0f} km of edges")


if __name__ == "__main__":
    main()
