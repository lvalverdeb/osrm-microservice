import requests
import folium
import random
import sys

# Configuration
API_URL = "http://10.211.55.28:8080"

def get_random_color():
    """Generate a random hex color for each vehicle."""
    return "#{:06x}".format(random.randint(0, 0xFFFFFF))

from folium.plugins import MarkerCluster

def visualize_vrp():
    # Warehouse Presets
    warehouses = [
        {"name": "Guadalupe (San Jose)", "latitude": 9.9472, "longitude": -84.0531},
        {"name": "Grecia", "latitude": 10.0734, "longitude": -84.3121},
        {"name": "Guapiles", "latitude": 10.2128, "longitude": -83.7847},
        {"name": "San Carlos", "latitude": 10.3228, "longitude": -84.4253},
        {"name": "Perez Zeledon", "latitude": 9.3734, "longitude": -83.7029},
    ]
    
    # Generate some stops relative to warehouses
    stops = []
    for wh in warehouses:
        for _ in range(10): # 10 stops per warehouse
            stops.append({
                "latitude": wh["latitude"] + random.uniform(-0.05, 0.05),
                "longitude": wh["longitude"] + random.uniform(-0.05, 0.05)
            })

    depots = [{"latitude": w["latitude"], "longitude": w["longitude"]} for w in warehouses]
    payload = {
        "depots": depots,
        "stops": stops
    }

    print(f"Requesting VRP solution for {len(stops)} stops and {len(payload['depots'])} warehouses...")
    
    try:
        response = requests.post(f"{API_URL}/vrp", json=payload)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error connecting to API: {e}")
        print("Make sure the OSRM microservice is running at http://localhost:8000")
        sys.exit(1)

    # Initialize Map
    m = folium.Map(location=[9.93, -84.10], zoom_start=9)
    marker_cluster = MarkerCluster().add_to(m)

    # Draw Depots
    for i, depot in enumerate(payload["depots"]):
        folium.Marker(
            [depot["latitude"], depot["longitude"]],
            popup=f"Warehouse {i}",
            icon=folium.Icon(color="red", icon="home")
        ).add_to(m)

    # Draw Routes
    colors = ['blue', 'green', 'purple', 'orange', 'darkred', 'lightred', 'beige', 'darkblue', 'darkgreen', 'cadetblue', 'darkpurple', 'white', 'pink', 'lightblue', 'lightgreen', 'gray', 'black', 'lightgray']
    
    for route in data["routes"]:
        color = colors[route["vehicle_id"] % len(colors)]
        vehicle_id = route["vehicle_id"]
        
        # Route geometry
        geom = route["route_geometry"]
        if geom["type"] == "LineString":
            points = [[p[1], p[0]] for p in geom["coordinates"]]
            folium.PolyLine(points, color=color, weight=4, opacity=0.7).add_to(m)
        
        # Mark assigned stops in cluster
        for stop_idx in route["stops_indices"]:
            stop = stops[stop_idx]
            folium.CircleMarker(
                [stop["latitude"], stop["longitude"]],
                radius=5,
                color=color,
                fill=True,
                popup=f"Stop {stop_idx}"
            ).add_to(marker_cluster)

    m.save("examples/vrpvrp_map.html")
    print("VRP Visualization saved to vrp_map.html")
    print(f"Total Fleet Distance: {data['total_distance']/1000:.2f} km")
    print(f"Total Fleet Duration: {data['total_duration']/60:.2f} min")

if __name__ == "__main__":
    visualize_vrp()
