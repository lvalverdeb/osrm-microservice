
import httpx
from config import settings

# Configuration - set OSRM_API_URL env var to point to your host
API_BASE_URL = settings.OSRM_API_URL

def download_vector_tile():
    # Tile coordinates (z, x, y) in the standard Web Mercator (slippy map) scheme.
    # This tile covers San Jose, Costa Rica (~9.928 N, -84.091 E). The previous
    # values (2197, 3991) resolved to 4.6 N / -83.45 E -- open Pacific, ~590 km
    # offshore -- so the gateway answered 200 with a zero-byte tile.
    zoom = 13
    x = 2182
    y = 3868
    
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
