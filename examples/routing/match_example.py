# /// script
# dependencies = [
#   "httpx",
#   "folium",
# ]
# ///

import os
import httpx
import folium

# Configuration - set OSRM_API_URL env var to point to your host
API_BASE_URL = os.environ.get("OSRM_API_URL", "http://10.211.55.28:8080")

# Example GPS trace (simulating a car driving around San Jose)
# These points might be noisy or slightly off the road
GPS_TRACE = [
    {"longitude": -84.0907, "latitude": 9.9281, "timestamp": 1713000000, "accuracy_meters": 10.0},
    {"longitude": -84.0880, "latitude": 9.9300, "timestamp": 1713000030, "accuracy_meters": 15.0},
    {"longitude": -84.0850, "latitude": 9.9315, "timestamp": 1713000060, "accuracy_meters": 8.0},
    {"longitude": -84.0833, "latitude": 9.9333, "timestamp": 1713000090, "accuracy_meters": 5.0}
]

def map_match_trace():
    print("Sending raw GPS trace to OSRM /match endpoint...")
    payload = {
        "breadcrumbs": GPS_TRACE,
        "profile": "driving",
        "geometries": "geojson",
        "tidy": True,
        "steps": True
    }
    
    response = httpx.post(f"{API_BASE_URL}/match", json=payload, timeout=10)
    response.raise_for_status()
    return response.json()

def main():
    try:
        match_data = map_match_trace()
        
        matchings = match_data.get("matchings", [])
        if not matchings:
            print("No match found for the given trace.")
            return
            
        print(f"Successfully matched trace! Confidence: {matchings[0].get('confidence')}")
        
        # Visualize the result
        m = folium.Map(location=[9.9300, -84.0880], zoom_start=15, tiles="CartoDB positron")
        
        # 1. Plot the raw, noisy GPS points (Red dots)
        for point in GPS_TRACE:
            folium.CircleMarker(
                location=[point["latitude"], point["longitude"]],
                radius=5,
                color="red",
                fill=True,
                popup="Raw GPS"
            ).add_to(m)
            
        # 2. Plot the OSRM matched geometry (Blue line)
        matched_geometry = matchings[0]["geometry"]
        folium.GeoJson(
            matched_geometry,
            name="Matched Route",
            style_function=lambda x: {'color': 'blue', 'weight': 5, 'opacity': 0.7}
        ).add_to(m)
        
        output_file = "match_example_map.html"
        m.save(output_file)
        print(f"\nMap saved to {output_file}. Open it in your browser to compare raw vs matched traces.")

    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.status_code}")
        print(e.response.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
