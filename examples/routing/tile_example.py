# /// script
# dependencies = [
#   "httpx",
# ]
# ///

import os
import httpx

# Configuration - set OSRM_API_URL env var to point to your host
API_BASE_URL = os.environ.get("OSRM_API_URL", "http://10.211.55.28:8080")

def download_vector_tile():
    # Tile coordinates (z, x, y)
    # 13 is the zoom level. 2197, 3991 are the x,y tile coordinates (near San Jose, Costa Rica)
    zoom = 13
    x = 2197
    y = 3991
    
    url = f"{API_BASE_URL}/tile/driving/{zoom}/{x}/{y}.mvt"
    print(f"Downloading Mapbox Vector Tile (MVT) from: {url}")
    
    # MVT tiles are binary data
    response = httpx.get(url, timeout=10)
    
    if response.status_code == 200:
        output_file = f"osrm_routing_graph_{zoom}_{x}_{y}.mvt"
        with open(output_file, "wb") as f:
            f.write(response.content)
            
        print(f"\nSuccess! Saved routing graph vector tile to {output_file}")
        print(f"Size: {len(response.content)} bytes.")
        print("Note: MVT is a binary format. You can use tools like 'tippecanoe' or mapbox-gl-js to visualize it.")
    else:
        print(f"Failed to download tile. HTTP {response.status_code}")
        print(response.text)

def main():
    try:
        download_vector_tile()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
