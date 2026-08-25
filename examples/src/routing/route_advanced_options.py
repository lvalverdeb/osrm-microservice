"""
Advanced Route Options Demo

Explores OSRM routing features beyond the basics:
  1. Alternatives with route comparison
  2. Bearing constraints (force specific entry angles)
  3. Road class exclusion (avoid tolls/motorways)
  4. continue_straight behavior
  5. Step-by-step turn instructions with annotations

Usage:
    uv run --package osrm-api-gateway-examples examples/src/routing/route_advanced_options.py

Requires:
    - OSRM API Gateway running at http://localhost:8000
"""

import os

import folium
import httpx
from config import settings

API_BASE_URL = settings.OSRM_API_URL


def fetch_route(payload: dict, label: str) -> dict:
    url = f"{API_BASE_URL}/route"
    print(f"\n[{label}] POST {url}")
    print(f"  Payload keys: {', '.join(k for k in payload if k not in ('waypoints',))}")
    resp = httpx.post(url, json=payload, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    print(f"  Routes returned: {len(data.get('routes', []))}")
    return data


def main():
    origin = {"longitude": -84.078, "latitude": 9.932}
    destination = {"longitude": -84.150, "latitude": 9.940}
    base_payload = {"origin": origin, "destination": destination, "waypoints": []}

    # --- 1. Default route (single, no alternatives) ---
    fetch_route({**base_payload, "alternatives": False}, "Single route, no alternates")

    # --- 2. Request 3 alternatives ---
    r2 = fetch_route({**base_payload, "alternatives": 3}, "3 alternatives")

    # --- 3. Bearing constraint: force departure heading NE (45 deg, ±10 deg tolerance) ---
    bearing_payload = {
        **base_payload,
        "alternatives": False,
        "bearings": ["45,10", None],
    }
    fetch_route(bearing_payload, "Bearing constraint (origin → NE 45°±10°)")

    # --- 4. Exclude toll roads ---
    exclude_payload = {**base_payload, "alternatives": 1, "exclude": ["toll"]}
    fetch_route(exclude_payload, "Avoid toll roads")

    # --- 5. continue_straight = false (force turns at waypoints) ---
    waypoint = {"longitude": -84.110, "latitude": 9.935}
    straight_payload = {
        **base_payload,
        "waypoints": [waypoint],
        "continue_straight": "false",
        "alternatives": False,
    }
    fetch_route(straight_payload, "continue_straight=false at waypoint")

    # --- 6. Steps + full annotations ---
    steps_payload = {
        **base_payload,
        "steps": True,
        "annotations": "distance,duration,nodes,speed",
    }
    r6 = fetch_route(steps_payload, "Steps + full annotations")
    route = r6.get("routes", [{}])[0]
    leg = route.get("legs", [{}])[0]
    steps = leg.get("steps", [])
    ann = leg.get("annotation", {})
    print(f"  Turn-by-turn steps: {len(steps)}")
    print(f"  Annotation segments: {len(ann.get('duration', []))}")
    if steps:
        s = steps[0]
        print(f"  First instruction: \"{s.get('maneuver', {}).get('type', '?')}\" "
              f"on {s.get('name', 'unnamed road')} for {s['distance']/1000:.2f} km")
    if ann.get("speed"):
        avg_speed = sum(ann["speed"]) / len(ann["speed"])
        print(f"  Average annotated speed: {avg_speed * 3.6:.1f} km/h")

    # --- Visualize the alternatives on a map ---
    m = folium.Map(location=[origin["latitude"], origin["longitude"]], zoom_start=13)
    colors = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"]

    for i, route in enumerate(r2.get("routes", [])):
        color = colors[i % len(colors)]
        label = "Primary" if i == 0 else f"Alt {i}"
        points = [[p[1], p[0]] for p in route["geometry"]["coordinates"]]
        dur_min = route["duration"] / 60
        dist_km = route["distance"] / 1000
        folium.PolyLine(
            points, color=color, weight=4, opacity=0.7,
            popup=f"{label}: {dist_km:.1f}km, {dur_min:.0f}min",
            tooltip=label
        ).add_to(m)

    folium.Marker([origin["latitude"], origin["longitude"]],
                  popup="Start", icon=folium.Icon(color="green")).add_to(m)
    folium.Marker([destination["latitude"], destination["longitude"]],
                  popup="End", icon=folium.Icon(color="red")).add_to(m)

    out = "examples/src/routing/advanced_routes_map.html"
    m.save(out)
    print(f"\nMap saved to {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
