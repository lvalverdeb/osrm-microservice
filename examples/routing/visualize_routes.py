# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "folium",
#     "httpx",
# ]
# ///

import httpx
import folium
import json
import os

# Configuration
API_URL = "http://10.211.55.28:8080/route"
# Use coordinates in San Jose that are likely to have multiple paths
ORIGIN = {"longitude": -84.078, "latitude": 9.932}
DESTINATION = {"longitude": -84.150, "latitude": 9.940}

def create_map():
    """Fetches routes from the OSRM microservice and generates an interactive map."""
    payload = {
        "origin": ORIGIN,
        "destination": DESTINATION,
        "waypoints": [],
        "alternatives": 3  # Request up to 3 alternates
    }
    
    print(f"Requesting routes from {API_URL}...")
    try:
        response = httpx.post(API_URL, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error calling API: {e}")
        print("Ensure the OSRM microservice is running locally (uvicorn app.main:app).")
        return

    routes = data.get("routes", [])
    if not routes:
        print("No routes found.")
        return

    print(f"Found {len(routes)} routes. Generating map...")
    
    # Initialize map centered at origin
    m = folium.Map(location=[ORIGIN["latitude"], ORIGIN["longitude"]], zoom_start=13)
    
    # Colors for different routes
    colors = ["#2563eb", "#10b981", "#f59e0b", "#ef4444"]
    
    for i, route in enumerate(routes):
        color = colors[i % len(colors)]
        title = "Primary Route" if i == 0 else f"Alternative {i}"
        
        # OSRM returns GeoJSON coordinates as [lon, lat]
        points = [[p[1], p[0]] for p in route["geometry"]["coordinates"]]
        
        distance_km = route["distance"] / 1000
        duration_min = route["duration"] / 60
        
        popup_text = (
            f"<b>{title}</b><br>"
            f"Distance: {distance_km:.2f} km<br>"
            f"Duration: {duration_min:.1f} min"
        )
        
        folium.PolyLine(
            points,
            color=color,
            weight=5,
            opacity=0.8,
            popup=folium.Popup(popup_text, max_width=300),
            tooltip=f"{title}: {distance_km:.2f}km, {duration_min:.1f}min"
        ).add_to(m)

    # Add markers for start and end
    folium.Marker(
        [ORIGIN["latitude"], ORIGIN["longitude"]], 
        popup="Start", 
        icon=folium.Icon(color="green", icon="play")
    ).add_to(m)
    
    folium.Marker(
        [DESTINATION["latitude"], DESTINATION["longitude"]], 
        popup="End", 
        icon=folium.Icon(color="red", icon="stop")
    ).add_to(m)

    output_file = "examples/routing/map.html"
    m.save(output_file)
    print(f"Success! Map saved to {os.path.abspath(output_file)}")

if __name__ == "__main__":
    # Check if folium and httpx are installed
    try:
        import folium
        import httpx
    except ImportError:
        print("Missing dependencies. Install them with: pip install folium httpx")
        exit(1)
        
    create_map()
