import requests
import json
import random
import folium

# Configuration
API_URL = "http://10.211.55.28:8080"
OUTPUT_FILE = "examples/clustering/simple_vrp_map.html"

def generate_multi_vehicle_data(num_depots=10, stops_per_depot=30):
    """Generate sample data for a larger-scale VRP simulation."""
    # Center around San José, Costa Rica
    base_lat, base_lon = 9.9281, -84.0907
    
    depots = []
    for i in range(num_depots):
        depots.append({
            "id": f"DEPOT-{i}",
            "latitude": base_lat + random.uniform(-0.05, 0.05),
            "longitude": base_lon + random.uniform(-0.05, 0.05)
        })
        
    stops = []
    for i in range(num_depots * stops_per_depot):
        stops.append({
            "id": f"ORD-{1000 + i}",
            "latitude": base_lat + random.uniform(-0.1, 0.1),
            "longitude": base_lon + random.uniform(-0.1, 0.1)
        })
        
    return depots, stops

def run_enhanced_vrp_demo():
    """Run a 10-vehicle VRP simulation and generate a map visualization."""
    
    print("--- Generating 10-Vehicle VRP Simulation Data ---")
    depots, stops = generate_multi_vehicle_data(num_depots=10, stops_per_depot=30)
    
    payload = {
        "depots": depots,
        "stops": stops,
        "clustering_mode": "travel_time",
        "capacity": 35  # Max 35 packages per vehicle
    }
    
    print(f"Submitting request: {len(depots)} depots, {len(stops)} stops...")
    
    try:
        response = requests.post(f"{API_URL}/vrp", json=payload, timeout=60)
        response.raise_for_status()
        results = response.json()
        
        print("\nOptimization Complete!")
        print(f"Total Distance: {results['total_distance']/1000:.2f} km")
        print(f"Total Duration: {results['total_duration']/3600:.2f} hours")
        
        # --- MAP VISUALIZATION ---
        print(f"\nGenerating Map: {OUTPUT_FILE}")
        m = folium.Map(location=[9.9281, -84.0907], zoom_start=11)
        
        # Color palette for different vehicles
        colors = ['red', 'blue', 'green', 'purple', 'orange', 'darkred', 'lightred', 'beige', 'darkblue', 'darkgreen']
        
        # 1. Plot Depots
        for i, d in enumerate(depots):
            folium.Marker(
                [d["latitude"], d["longitude"]],
                popup=f"Depot {i}",
                icon=folium.Icon(color='black', icon='home')
            ).add_to(m)
            
        # 2. Plot Routes and Stops
        for i, route in enumerate(results["routes"]):
            color = colors[i % len(colors)]
            v_id = route["vehicle_id"]
            
            print(f"Vehicle {v_id}: {len(route['stop_ids'])} stops assigned.")
            
            # Draw actual road geometry
            if "route_geometry" in route and route["route_geometry"]:
                # GeoJSON is [lon, lat], Folium needs [lat, lon]
                path = [[p[1], p[0]] for p in route["route_geometry"]["coordinates"]]
                folium.PolyLine(path, color=color, weight=3, opacity=0.8, popup=f"Vehicle: {v_id}").add_to(m)
            
            # Draw assigned stops (using our unique IDs from the response!)
            # Note: We need to find the coordinates of these IDs
            id_to_coords = {s["id"]: [s["latitude"], s["longitude"]] for s in stops}
            
            for stop_id in route["stop_ids"]:
                coords = id_to_coords.get(stop_id)
                if coords:
                    folium.CircleMarker(
                        coords,
                        radius=3,
                        color=color,
                        fill=True,
                        popup=f"Stop ID: {stop_id} (Vehicle: {v_id})"
                    ).add_to(m)
        
        m.save(OUTPUT_FILE)
        print("Success! Open the HTML file to view the results.")
        
    except requests.exceptions.RequestException as e:
        print(f"\nError calling API ({e}):")
        if hasattr(e.response, 'text'):
            print(f"Detail: {e.response.text}")

if __name__ == "__main__":
    run_enhanced_vrp_demo()
