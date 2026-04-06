import requests
import json
from typing import List, Dict, Any

# --- Configuration ---
API_BASE_URL = "http://10.211.55.28:8080"
MATRIX_URL = f"{API_BASE_URL}/matrix"

def get_distance_duration_matrix(locations: List[Dict[str, float]], sources: List[int] = None, destinations: List[int] = None) -> Dict[str, Any]:
    """
    Fetch the distance and duration matrix for the given locations.
    
    Args:
        locations: List of dicts with 'latitude' and 'longitude'.
        sources: List of indices to use as origins.
        destinations: List of indices to use as destinations.
        
    Returns:
        JSON response with 'durations' and 'distances'.
    """
    payload = {
        "coordinates": locations,
        "sources": sources,
        "destinations": destinations
    }
    
    print(f"Submitting matrix request for {len(locations)} locations...")
    response = requests.post(MATRIX_URL, json=payload)
    response.raise_for_status()
    return response.json()

def main():
    # --- Define Test Locations (San José, Costa Rica) ---
    locations = [
        {"id": "San José Centro", "latitude": 9.9333, "longitude": -84.0833},
        {"id": "Alajuela",        "latitude": 10.0167, "longitude": -84.2167},
        {"id": "Heredia",         "latitude": 9.9981, "longitude": -84.1197},
        {"id": "Cartago",         "latitude": 9.8644, "longitude": -83.9194}
    ]
    
    # --- Execute Matrix Request ---
    # We want a full N x N matrix, so we don't specify sources/destinations.
    results = get_distance_duration_matrix(locations)
    
    durations = results.get("durations", [])
    distances = results.get("distances", [])
    
    # --- Display Results ---
    print("\n--- TRAVEL DURATION MATRIX (Minutes) ---")
    header = "From \\ To".ljust(20) + "".join([loc["id"].rjust(15) for loc in locations])
    print(header)
    print("-" * len(header))
    
    for i, row in enumerate(durations):
        row_str = locations[i]["id"].ljust(20)
        for val in row:
            mins = val / 60.0 if val is not None else 0
            row_str += f"{mins:15.1f}"
        print(row_str)
        
    print("\n--- TRAVEL DISTANCE MATRIX (Kilometers) ---")
    header = "From \\ To".ljust(20) + "".join([loc["id"].rjust(15) for loc in locations])
    print(header)
    print("-" * len(header))
    
    for i, row in enumerate(distances):
        row_str = locations[i]["id"].ljust(20)
        for val in row:
            kms = val / 1000.0 if val is not None else 0
            row_str += f"{kms:15.2f}"
        print(row_str)

if __name__ == "__main__":
    main()
