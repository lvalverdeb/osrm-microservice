import json
import random

def generate_example_payload():
    # Warehouses
    depots = [
        {"latitude": 9.9469, "longitude": -84.0558},    # Guadalupe
        {"latitude": 10.0734, "longitude": -84.3121},   # Grecia
        {"latitude": 10.2128, "longitude": -83.7847},   # Guapiles
        {"latitude": 10.3228, "longitude": -84.4253},   # San Carlos
        {"latitude": 10.6333, "longitude": -85.5333},   # Liberia
        {"latitude": 9.3734, "longitude": -83.7029},    # Perez Zeledon
    ]
    
    # Generate 50 stops near these warehouses
    stops = []
    for _ in range(50):
        base = random.choice(depots)
        # Random offset within approx ~10km (0.1 decimal degrees is roughly 11km)
        lat = base["latitude"] + random.uniform(-0.05, 0.05)
        lon = base["longitude"] + random.uniform(-0.05, 0.05)
        stops.append({"latitude": lat, "longitude": lon})
        
    payload = {
        "depots": depots,
        "stops": stops,
        "capacity": 35
        # max_radius_km removed to allow global road-distance clustering
    }
    
    with open("examples/clustering/stress_test_payload.json", "w") as f:
        json.dump(payload, f, indent=2)
    print("Generated examples/clustering/stress_test_payload.json with 6 depots and 50 stops.")

if __name__ == "__main__":
    generate_example_payload()
