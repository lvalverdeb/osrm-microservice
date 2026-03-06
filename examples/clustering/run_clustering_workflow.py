import requests
import folium
from folium.plugins import MarkerCluster
import random
import json
import os

# Configuration
API_URL = "http://10.211.55.28:8080"
OUTPUT_DIR = "examples/clustering"
MAP_FILE = f"{OUTPUT_DIR}/clustering_results_map.html"
PAYLOAD_FILE = f"{OUTPUT_DIR}/clustering_payload.json"

# Core Warehouse Configuration (6 Depots)
WAREHOUSES = [
    {"name": "Guadalupe (San Jose)", "latitude": 9.9472, "longitude": -84.0531},
    {"name": "Grecia (Alajuela)", "latitude": 10.0734, "longitude": -84.3121},
    {"name": "Guapiles (Limon)", "latitude": 10.2128, "longitude": -83.7847},
    {"name": "San Carlos (Alajuela North)", "latitude": 10.3228, "longitude": -84.4253},
    {"name": "Liberia (Guanacaste)", "latitude": 10.6333, "longitude": -85.5333},
    {"name": "Perez Zeledon (San Jose South)", "latitude": 9.3734, "longitude": -83.7029},
]

# Expanded Province-District Coordinates Hubs
PROVINCES_HUBS = {
    "San Jose": [
        {"name": "San Jose Central", "latitude": 9.9333, "longitude": -84.0833, "valle_central": True},
        {"name": "Escazu", "latitude": 9.9200, "longitude": -84.1400, "valle_central": True},
        {"name": "Desamparados", "latitude": 9.8900, "longitude": -84.0600, "valle_central": True},
        {"name": "Santa Ana", "latitude": 9.9300, "longitude": -84.1800, "valle_central": True},
        {"name": "Perez Zeledon", "latitude": 9.3734, "longitude": -83.7029, "valle_central": False},
        {"name": "Puriscal", "latitude": 9.8500, "longitude": -84.3200, "valle_central": False},
    ],
    "Alajuela": [
        {"name": "Alajuela Central", "latitude": 10.0163, "longitude": -84.2139, "valle_central": True},
        {"name": "Grecia", "latitude": 10.0734, "longitude": -84.3121, "valle_central": True},
        {"name": "San Ramon", "latitude": 10.0800, "longitude": -84.4700, "valle_central": True},
        {"name": "San Carlos", "latitude": 10.3228, "longitude": -84.4253, "valle_central": False},
        {"name": "Upala", "latitude": 10.8833, "longitude": -85.0167, "valle_central": False},
    ],
    "Cartago": [
        {"name": "Cartago Central", "latitude": 9.8644, "longitude": -83.9194, "valle_central": True},
        {"name": "Paraiso", "latitude": 9.8378, "longitude": -83.8656, "valle_central": True},
        {"name": "La Union", "latitude": 9.9100, "longitude": -83.9800, "valle_central": True},
        {"name": "Turrialba", "latitude": 9.9048, "longitude": -83.6841, "valle_central": False},
    ],
    "Heredia": [
        {"name": "Heredia Central", "latitude": 9.9982, "longitude": -84.1167, "valle_central": True},
        {"name": "San Rafael", "latitude": 10.0100, "longitude": -84.1000, "valle_central": True},
        {"name": "Belen", "latitude": 9.9833, "longitude": -84.1833, "valle_central": True},
        {"name": "Sarapiqui", "latitude": 10.4503, "longitude": -84.0089, "valle_central": False},
    ],
    "Guanacaste": [
        {"name": "Liberia", "latitude": 10.6350, "longitude": -85.4407, "valle_central": False},
        {"name": "Canas", "latitude": 10.4310, "longitude": -85.0931, "valle_central": False},
        {"name": "Nicoya", "latitude": 10.1500, "longitude": -85.4500, "valle_central": False},
        {"name": "Santa Cruz", "latitude": 10.2611, "longitude": -85.5847, "valle_central": False},
    ],
    "Puntarenas": [
        {"name": "Puntarenas Central", "latitude": 9.9763, "longitude": -84.8384, "valle_central": False},
        {"name": "Esparza", "latitude": 9.9912, "longitude": -84.6647, "valle_central": False},
        {"name": "Jaco", "latitude": 9.6144, "longitude": -84.6289, "valle_central": False},
        {"name": "Quepos", "latitude": 9.4300, "longitude": -84.1600, "valle_central": False},
    ],
    "Limon": [
        {"name": "Limon Centre", "latitude": 9.9913, "longitude": -83.0415, "valle_central": False},
        {"name": "Guapiles", "latitude": 10.2128, "longitude": -83.7847, "valle_central": False},
        {"name": "Siquirres", "latitude": 10.1000, "longitude": -83.5000, "valle_central": False},
        {"name": "Bribri", "latitude": 9.6200, "longitude": -82.8500, "valle_central": False},
    ]
}

def is_in_costa_rica(lat, lon):
    """Precise ray casting algorithm for Costa Rica mainland."""
    poly = [
        (11.217, -85.1), (11.0, -83.6), (10.2, -82.7), 
        (9.5, -82.55), (8.1, -82.8), (8.046, -83.3), 
        (8.4, -83.8), (10.2, -85.94), (11.1, -85.8)
    ]
    n = len(poly)
    inside = False
    px, py = lon, lat
    p1x, p1y = poly[0][1], poly[0][0]
    for i in range(n + 1):
        p2x, p2y = poly[i % n][1], poly[i % n][0]
        if py > min(p1y, p2y):
            if py <= max(p1y, p2y):
                if px <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (py - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or px <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def generate_payload(total_stops=6500):
    """Generate 6500 stops with 60/40 Valle Central distribution and precision snapping."""
    depots = [{"latitude": w["latitude"], "longitude": w["longitude"]} for w in WAREHOUSES]
    
    stops = []
    metadata = []
    
    valle_hubs = []
    rest_hubs = []
    
    for prov, hubs in PROVINCES_HUBS.items():
        for hub in hubs:
            h = hub.copy(); h["province"] = prov
            if hub.get("valle_central"): valle_hubs.append(h)
            else: rest_hubs.append(h)
                
    valle_target = int(total_stops * 0.6)
    rest_target = total_stops - valle_target
    
    print(f"Generating {valle_target} stops for Valle Central districts...")
    while len(stops) < valle_target:
        hub = random.choice(valle_hubs)
        # Tight offset (approx 1km) to ensure road snapping
        lat = hub["latitude"] + random.uniform(-0.01, 0.01)
        lon = hub["longitude"] + random.uniform(-0.01, 0.01)
        if is_in_costa_rica(lat, lon):
            stops.append({"latitude": lat, "longitude": lon})
            metadata.append({"province": hub["province"], "district": hub["name"], "zone": "Valle Central"})
        
    print(f"Generating {rest_target} stops for Rest of Country districts...")
    while len(stops) < total_stops:
        hub = random.choice(rest_hubs)
        # Offset ~2km for rural areas
        lat = hub["latitude"] + random.uniform(-0.02, 0.02)
        lon = hub["longitude"] + random.uniform(-0.02, 0.02)
        if is_in_costa_rica(lat, lon):
            stops.append({"latitude": lat, "longitude": lon})
            metadata.append({"province": hub["province"], "district": hub["name"], "zone": "Rest of Country"})
        
    payload = {
        "depots": depots,
        "stops": stops,
        "capacity": 35
    }
    
    with open(PAYLOAD_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    
    return payload, metadata

def run_clustering(payload, mode="road", hysteresis=2000.0):
    """Send request to the /vrp/allocate endpoint."""
    url = f"http://10.211.55.28:8080/vrp/allocate"
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
    import folium
    
    m = folium.Map(location=[9.7489, -83.7534], zoom_start=8)
    colors = ['blue', 'green', 'red', 'purple', 'orange', 'darkred']
    
    for i, depot in enumerate(payload["depots"]):
        name = WAREHOUSES[i]['name']
        folium.Marker(
            [depot["latitude"], depot["longitude"]],
            popup=f"Warehouse: {name}",
            icon=folium.Icon(color='black', icon='home')
        ).add_to(m)
        
    allocations = results.get("allocations", {})
    for d_idx_str, stop_indices in allocations.items():
        d_idx = int(d_idx_str)
        depot_coords = [payload["depots"][d_idx]["latitude"], payload["depots"][d_idx]["longitude"]]
        color = colors[d_idx % len(colors)]
        for s_idx in stop_indices:
            stop = payload["stops"][s_idx]
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
    payload, metadata = generate_payload(6500)
    
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
