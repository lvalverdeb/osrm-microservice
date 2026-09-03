import json
import sys
from functools import lru_cache
from pathlib import Path

import folium
import requests
from config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

# Configuration
API_URL = settings.OSRM_API_URL
OUTPUT_DIR = "examples/src/clustering"
MAP_FILE = f"{OUTPUT_DIR}/clustering_results_map.html"
PAYLOAD_FILE = f"{OUTPUT_DIR}/clustering_payload.json"

# The gateway rejects more stops than VRP_MAX_STOPS (default 2000) with a 422.
# See docs/configuration.md; raise both together if you need a bigger run.
MAX_STOPS = 2000

# The depots, the districts and the country outline all used to be written out
# here: six hard-coded warehouses, a fifty-entry table of province hubs, and a
# ray-casting polygon to reject points that fell in the sea. The corpus has all
# three -- the same six depots, 50,000 deliveries placed around the country's
# real hubs, and each one's province and hub -- so none of it needs restating.
#
# Not snapped, though: this corpus was generated without `--engine`, so its
# `snapped_to_road_network` is false and a few points sit up to ~190 m off the
# network. The gateway snaps them when it builds a matrix, which is why a
# `SnapWarning` is normal here rather than a fault.

@lru_cache(maxsize=1)
def depot_names() -> tuple[str, ...]:
    """The depots' names, in payload order, for labelling the map.

    The `/vrp` payload carries coordinates only, so the names come from the
    corpus rather than from a copy of them kept beside it.
    """
    return tuple(d["name"] for d in dataset.load().depots)


def generate_payload(total_stops=MAX_STOPS):
    """Real deliveries spread across every depot, in the shape `/vrp` expects.

    Args:
        total_stops: How many deliveries to take, shared across the depots.

    Returns:
        `(payload, metadata)`. The metadata carries each stop's province, hub
        and whether it is in the Greater Metropolitan Area -- the corpus's own
        `gam` flag, which is what the old "Valle Central" split was reaching
        for when it generated 60% of its stops around a list of hubs.

    `contested` rather than `around_each_depot`, and the difference is the
    whole example. Taking each depot's nearest share gives a payload where
    every stop is already nearest the depot that will get it: both clustering
    modes then return exactly 333 stops each and agree on every one of them,
    so a comparison of road distance against travel time compares nothing.
    These are the stops between depots, and the two modes disagree about
    roughly one in sixteen of them.
    """
    corpus = dataset.load()
    deliveries, depots = corpus.contested(total_stops)

    stops, metadata = [], []
    for delivery in deliveries:
        stops.append({"id": delivery["product_id"],
                      "latitude": delivery["latitude"],
                      "longitude": delivery["longitude"]})
        metadata.append({
            "id": delivery["product_id"], "province": delivery["province"],
            "district": delivery["hub"],
            "zone": "Valle Central" if delivery["gam"] else "Rest of Country"})

    inside = sum(1 for m in metadata if m["zone"] == "Valle Central")
    print(f"{len(stops)} real deliveries across {len(depots)} depots: "
          f"{inside} in the Valle Central, {len(stops) - inside} outside "
          f"({inside / max(len(stops), 1):.0%}/"
          f"{1 - inside / max(len(stops), 1):.0%}).")
    print("That split is the corpus's, not a target -- the old generator aimed "
          "for 60/40 and got it by construction.")

    payload = {"depots": [{"latitude": d["latitude"],
                           "longitude": d["longitude"]} for d in depots],
               "stops": stops}
    with open(PAYLOAD_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    return payload, metadata


def run_clustering(payload, mode="road", hysteresis=2000.0):
    """Send request to the /vrp/allocate endpoint."""
    url = f"{API_URL}/vrp/allocate"
    print(f"Calling {url} [MODE: {mode}, HYST: {hysteresis}m] with {len(payload['stops'])} stops...")
    
    payload_with_mode = payload.copy()
    payload_with_mode["clustering_mode"] = mode
    payload_with_mode["hysteresis_m"] = hysteresis
    
    try:
        response = requests.post(url, json=payload_with_mode, timeout=60)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None

def visualize_results(payload, results, output_file):
    """Generate a folium map with clustering spider-web lines."""
    
    m = folium.Map(location=[9.7489, -83.7534], zoom_start=8)
    colors = ['blue', 'green', 'red', 'purple', 'orange', 'darkred']
    
    for i, depot in enumerate(payload["depots"]):
        name = depot_names()[i]
        folium.Marker(
            [depot["latitude"], depot["longitude"]],
            popup=f"Warehouse: {name}",
            icon=folium.Icon(color='black', icon='home')
        ).add_to(m)
        
    # Create lookup for stops by ID or original index
    stop_lookup = {s.get("id", i): s for i, s in enumerate(payload["stops"])}
    
    allocations = results.get("allocations", {})
    for d_idx_str, stop_ids in allocations.items():
        d_idx = int(d_idx_str)
        depot_coords = [payload["depots"][d_idx]["latitude"], payload["depots"][d_idx]["longitude"]]
        color = colors[d_idx % len(colors)]
        for s_id in stop_ids:
            # Handle both string IDs and fallback integer indices
            stop = stop_lookup.get(s_id)
            if not stop:
                continue
            
            stop_coords = [stop["latitude"], stop["longitude"]]
            folium.PolyLine([depot_coords, stop_coords], color=color, weight=1, opacity=0.3).add_to(m)
            folium.CircleMarker(stop_coords, radius=1, color=color, fill=True, opacity=0.5).add_to(m)
            
    m.save(output_file)
    print(f"Map successfully generated: {output_file}")

def print_report(mode, hyst, payload, results, metadata):
    total_stops = len(payload["stops"])
    allocations = results.get("allocations", {})
    reachable_indices = set()
    for d_list in allocations.values():
        reachable_indices.update(d_list)
    reachable_count = len(reachable_indices)
    print(f"\n--- REPORT: {mode.upper()} DISTANCE (Hysteresis: {hyst}m) ---")
    print(f"Successfully Allocated: {reachable_count}/{total_stops} ({reachable_count/total_stops:.1%})")

def main():
    payload, metadata = generate_payload(MAX_STOPS)
    
    # Scenario A: Road Distance
    print("\n" + "="*60)
    print("RUNNING SCENARIO A: SHORTEST ROAD DISTANCE")
    print("="*60)
    results_dist = run_clustering(payload, mode="distance", hysteresis=2000.0)
    if results_dist and results_dist.get("code") == "Ok":
        map_dist = f"{OUTPUT_DIR}/clustering_results_road_distance.html"
        visualize_results(payload, results_dist, map_dist)
        print_report("distance", 2000, payload, results_dist, metadata)

    # Scenario B: Travel Time
    print("\n" + "="*60)
    print("RUNNING SCENARIO B: SHORTEST TRAVEL TIME")
    print("="*60)
    results_time = run_clustering(payload, mode="travel_time", hysteresis=2000.0)
    if results_time and results_time.get("code") == "Ok":
        map_time = f"{OUTPUT_DIR}/clustering_results_road_time.html"
        visualize_results(payload, results_time, map_time)
        print_report("travel_time", 2000, payload, results_time, metadata)

    print("\n" + "="*60)
    print("COMPARISON COMPLETE")
    print(f"1. Distance Map : {OUTPUT_DIR}/clustering_results_road_distance.html")
    print(f"2. Time Map     : {OUTPUT_DIR}/clustering_results_road_time.html")
    print("="*60)

if __name__ == "__main__":
    main()
