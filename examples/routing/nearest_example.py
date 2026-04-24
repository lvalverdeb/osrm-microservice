# /// script
# dependencies = [
#   "httpx",
# ]
# ///

import os
import httpx
import json

# Configuration - set OSRM_API_URL env var to point to your host
API_BASE_URL = os.environ.get("OSRM_API_URL", "http://10.211.55.28:8080")

def get_nearest_segment():
    print("Requesting the 3 nearest road segments to a specific coordinate...")
    
    # Coordinate slightly off the road
    payload = {
        "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
        "number": 3,
        "profile": "driving"
    }
    
    response = httpx.post(f"{API_BASE_URL}/nearest", json=payload, timeout=10)
    response.raise_for_status()
    return response.json()

def main():
    try:
        nearest_data = get_nearest_segment()
        
        waypoints = nearest_data.get("waypoints", [])
        if not waypoints:
            print("No nearby roads found.")
            return
            
        print(f"Found {len(waypoints)} nearest road segment(s):\n")
        
        for i, wp in enumerate(waypoints, 1):
            name = wp.get("name", "Unnamed Road")
            dist = wp.get("distance", 0.0)
            loc = wp.get("location", [])
            print(f"{i}. Road Name: '{name}'")
            print(f"   Distance from input: {dist:.2f} meters")
            print(f"   Snapped Coordinate: {loc}\n")

    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.status_code}")
        print(e.response.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
