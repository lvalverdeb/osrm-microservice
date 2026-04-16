import os
import requests
import folium
from folium.plugins import MarkerCluster
import random
import sys
import json

# Configuration
API_URL = os.environ.get("OSRM_API_URL", "http://localhost:8000")

# Warehouse Presets
WAREHOUSES = [
    {"name": "Guadalupe (San Jose)", "latitude": 9.9472, "longitude": -84.0531},
    {"name": "Grecia", "latitude": 10.0734, "longitude": -84.3121},
    {"name": "Guapiles", "latitude": 10.2128, "longitude": -83.7847},
    {"name": "San Carlos", "latitude": 10.3228, "longitude": -84.4253},
    {"name": "Liberia", "latitude": 10.6333, "longitude": -85.5333},
    {"name": "Perez Zeledon", "latitude": 9.3734, "longitude": -83.7029},
]

def is_in_costa_rica(lat, lon):
    """Refined polygon approximation to match precision bounds."""
    # Refined polygon approximation to match precision bounds
    poly = [
        (11.217119, -85.1), (11.0, -83.6), (10.2, -82.7), 
        (9.5, -82.553256), (8.1, -82.8), (8.046187, -83.3), 
        (8.4, -83.8), (10.2, -85.941725), (10.9, -85.9)
    ]
    # Ray casting algorithm
    n = len(poly)
    inside = False
    p1x, p1y = poly[0][1], poly[0][0]
    for i in range(n + 1):
        p2x, p2y = poly[i % n][1], poly[i % n][0]
        if lat > min(p1y, p2y):
            if lat <= max(p1y, p2y):
                if lon <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or lon <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def generate_random_stops(count=6500):
    """Generate random stops across Costa Rica mainland using precise bounds."""
    stops = []
    lat_min, lat_max = 8.046187, 11.217119
    lon_min, lon_max = -85.941725, -82.553256
    
    unique_locations_count = count // 5
    unique_locations = []
    
    while len(unique_locations) < unique_locations_count:
        lat = random.uniform(lat_min, lat_max)
        lon = random.uniform(lon_min, lon_max)
        if is_in_costa_rica(lat, lon):
            unique_locations.append({"latitude": lat, "longitude": lon})
            
    for _ in range(count):
        loc = random.choice(unique_locations)
        stops.append(loc)
        
    return stops

def run_stress_test():
    depots = [{"latitude": w["latitude"], "longitude": w["longitude"]} for w in WAREHOUSES]
    stops = generate_random_stops(2500)
    
    payload = {
        "depots": depots,
        "stops": stops,
        "capacity": 35,
        "max_radius_km": 25.0
    }
    
    print(f"Sending VRP scale test: 6 depots, 2500 stops...")
    print(f"Constraints: Capacity={payload['capacity']}, Max Radius={payload['max_radius_km']}km")
    print("This may take a moment as OSRM processes many matrix batches and TSP chunks.")
    
    try:
        response = requests.post(f"{API_URL}/vrp", json=payload, timeout=300)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"VRP Solved: {len(data['routes'])} total vehicle routes generated.")
    
    # Save the raw data for analysis
    with open("examples/vrp/stress_test_result.json", "w") as f:
        json.dump(data, f)
    
    print("Results saved to examples/vrp/stress_test_result.json. Now generating map...")
    return data, depots, stops

def generate_map(data, depots, stops):
    m = folium.Map(location=[9.8, -84.0], zoom_start=8)
    
    # Precise Bounding Box from User
    lat_min, lat_max = 8.046187, 11.217119
    lon_min, lon_max = -85.941725, -82.553256
    
    # Draw Bounding Box
    folium.Rectangle(
        bounds=[[lat_min, lon_min], [lat_max, lon_max]],
        color="black",
        weight=2,
        fill=False,
        dash_array='5, 5',
        popup="Costa Rica Constraints (BBox)"
    ).add_to(m)
    
    # Warehouse Marker Cluster
    wh_cluster = MarkerCluster(name="Warehouses").add_to(m)
    for i, wh in enumerate(WAREHOUSES):
        folium.Marker(
            [wh["latitude"], wh["longitude"]],
            popup=wh["name"],
            icon=folium.Icon(color="red", icon="home")
        ).add_to(wh_cluster)
    
    # Stops Marker Cluster
    stop_cluster = MarkerCluster(name="Delivery Stops").add_to(m)
    
    colors = [
        'blue', 'green', 'purple', 'orange', 'darkred', 
        'lightred', 'beige', 'darkblue', 'darkgreen', 'cadetblue', 
        'darkpurple', 'white', 'pink', 'lightblue', 'lightgreen', 
        'gray', 'black', 'lightgray'
    ]
    
    # To keep map readable, we only draw a subset of routes or just the lines
    for route in data["routes"]:
        vehicle_id = route["vehicle_id"]
        depot_idx = route["depot_index"]
        color = colors[vehicle_id % len(colors)]
        
        # Route geometry
        geom = route["route_geometry"]
        if geom["type"] == "LineString":
            points = [[p[1], p[0]] for p in geom["coordinates"]]
            folium.PolyLine(
                points,
                color=color,
                weight=2,
                opacity=0.6,
                tooltip=f"Vehicle {vehicle_id} (Depot {depot_idx})"
            ).add_to(m)
            
        # Add a sampling of stops to the cluster to avoid browser crash
        # 6500 points is a lot, but MarkerCluster handles it well.
        for stop_idx in route["stops_indices"][:100]: # Sample 100 per route
            stop = stops[stop_idx]
            folium.CircleMarker(
                [stop["latitude"], stop["longitude"]],
                radius=3,
                color=color,
                fill=True,
                popup=f"Stop {stop_idx} (V{vehicle_id})"
            ).add_to(stop_cluster)

    m.save("examples/vrp/stress_test_vrp_map.html")
    print("Map generated: examples/vrp/stress_test_vrp_map.html")

if __name__ == "__main__":
    result_data, depots, stops = run_stress_test()
    generate_map(result_data, depots, stops)
