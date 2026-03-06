# /// script
# dependencies = [
#   "httpx",
#   "folium",
# ]
# ///

import httpx
import folium
import json
import webbrowser
import os

# Configuration - update to your remote host if needed
API_BASE_URL = "http://10.211.55.28:8080" # Mapping to osrm-api-gateway

# Warehouse location (Start and End)
WAREHOUSE = {"longitude": -84.0911, "latitude": 9.9326} # Sabana

# Delivery Stops (scrambled order)
STOPS = [
    {"longitude": -84.0500, "latitude": 9.9333},  # San Pedro
    {"longitude": -84.0750, "latitude": 9.9167},  # Plaza Viquez
    {"longitude": -84.0833, "latitude": 9.9333},  # Central Park
    {"longitude": -84.0667, "latitude": 9.9500},  # Tournon
]

# Combined list for processing
COORDINATES = [WAREHOUSE] + STOPS

def get_route(coords):
    print(f"Requesting actual round-trip route (sequence as provided)...")
    # For a round trip in /route, the destination MUST be the origin
    payload = {
        "origin": coords[0],
        "destination": coords[0], # Return to Warehouse
        "waypoints": coords[1:],  # All stops are intermediate waypoints
        "alternatives": False
    }
    response = httpx.post(f"{API_BASE_URL}/route", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def get_trip(coords, roundtrip=True):
    print(f"Requesting TSP optimized trip (roundtrip={roundtrip})...")
    # For /trip, roundtrip=True automatically returns to the 'source' point
    payload = {
        "coordinates": coords,
        "roundtrip": roundtrip,
        "source": "first",
        "destination": "last" if not roundtrip else "any"
    }
    response = httpx.post(f"{API_BASE_URL}/trip", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def main():
    try:
        # Configuration for comparison
        ROUNDTRIP = True # Warehouse round trip
        
        # Fetch routes
        route_data = get_route(COORDINATES)
        trip_data = get_trip(COORDINATES, roundtrip=ROUNDTRIP)

        # Initialize map centered in San Jose
        m = folium.Map(location=[9.9333, -84.0833], zoom_start=14, tiles="cartodbpositron")

        # 1. Plot "Actual" Route (Red)
        geometry = route_data["routes"][0]["geometry"]
        distance = route_data['routes'][0]['distance'] / 1000
        duration = route_data['routes'][0]['duration'] / 60
        
        # OSRM returns [long, lat], Folium needs [lat, long]
        line_coords = [(p[1], p[0]) for p in geometry["coordinates"]]
        folium.PolyLine(
            line_coords, 
            color="red", 
            weight=5, 
            opacity=0.7,
            tooltip=f"Actual Round Trip: {distance:.2f}km, {duration:.1f}min"
        ).add_to(m)

        # 2. Plot "TSP Optimized" Trip (Green)
        trip_geom = trip_data["trips"][0]["geometry"]
        trip_dist = trip_data['trips'][0]['distance'] / 1000
        trip_dur = trip_data['trips'][0]['duration'] / 60
        
        trip_line_coords = [(p[1], p[0]) for p in trip_geom["coordinates"]]
        folium.PolyLine(
            trip_line_coords, 
            color="green", 
            weight=5, 
            opacity=0.8,
            dash_array='10',
            tooltip=f"TSP Optimized Round Trip: {trip_dist:.2f}km, {trip_dur:.1f}min"
        ).add_to(m)

        # Interpret the "best" sequence
        waypoints = trip_data["waypoints"]
        optimized_sequence = sorted([(w["waypoint_index"], i) for i, w in enumerate(waypoints)])
        
        sequence_list = []
        for pos, idx in optimized_sequence:
            if idx == 0:
                sequence_list.append("WAREHOUSE")
            else:
                sequence_list.append(f"Stop {idx}")
        
        if ROUNDTRIP:
            sequence_list.append("WAREHOUSE")
            
        sequence_str = " -> ".join(sequence_list)

        # Mark coordinates
        # Warehouse
        folium.Marker(
            [WAREHOUSE["latitude"], WAREHOUSE["longitude"]],
            popup="WAREHOUSE (Start/End)",
            icon=folium.Icon(color="darkblue", icon="home")
        ).add_to(m)

        # Stops
        for i, stop in enumerate(STOPS):
            idx = i + 1
            opt_rank = waypoints[idx]["waypoint_index"]
            folium.Marker(
                [stop["latitude"], stop["longitude"]],
                popup=f"Stop {idx}<br>Optimized Rank: {opt_rank}",
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(m)

        # Save and show
        output_file = "examples/benchmarking/comparison_map.html"
        m.save(output_file)
        
        print(f"\n--- TSP ROUND-TRIP Comparison ---")
        print(f"Warehouse: {WAREHOUSE['longitude']}, {WAREHOUSE['latitude']}")
        print(f"Stops: {len(STOPS)}")
        print(f"\nActual Route (Red):   {distance:.2f} km | {duration:.1f} min")
        print(f"TSP Optimized (Green): {trip_dist:.2f} km | {trip_dur:.1f} min")
        print(f"\nBest (Optimized) Sequence:")
        print(sequence_str)
        print(f"\nOptimization Gain: {distance - trip_dist:.2f} km SAVED")
        print(f"\nMap saved to {os.path.abspath(output_file)}")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
