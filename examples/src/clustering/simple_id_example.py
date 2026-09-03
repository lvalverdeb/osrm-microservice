import sys
from pathlib import Path

import folium
import requests
from config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

# Configuration
API_URL = settings.OSRM_API_URL
OUTPUT_FILE = "examples/src/clustering/simple_vrp_map.html"

def generate_multi_vehicle_data(stops=300):
    """Real depots and a share of the work near each.

    Args:
        stops: How many deliveries to take, shared across the depots.

    Returns:
        `(depots, stops)` in the shape `/vrp` expects, with the corpus's own
        delivery ids so the response's `stop_ids` can be looked back up.

    Both used to be `random.uniform` offsets around San Jose -- ten depots that
    were not the fleet's and stops that were not deliveries. The corpus has six
    real depots and the work around them, which is also why the vehicle count
    below is whatever capacity implies rather than a number chosen in advance.
    """
    corpus = dataset.load()
    deliveries, warehouses = corpus.around_each_depot(stops)
    depots = [{"id": f"DEPOT-{i}", "latitude": d["latitude"],
               "longitude": d["longitude"]}
              for i, d in enumerate(warehouses)]
    stops_out = [{"id": d["product_id"], "latitude": d["latitude"],
                  "longitude": d["longitude"]} for d in deliveries]
    return depots, stops_out


def run_enhanced_vrp_demo():
    """Run a multi-depot VRP over real deliveries and map the result."""
    
    print("--- Building the run from the delivery corpus ---")
    depots, stops = generate_multi_vehicle_data()
    
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
            if route.get("route_geometry"):
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
