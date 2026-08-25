"""
Error Handling Demo

Demonstrates how the API Gateway responds to various error conditions:
  1. OSRM backend unreachable (ConnectionError)
  2. Invalid coordinates (out of bounds → 422)
  3. Missing required fields (422 Validation Error)
  4. Invalid profile value (422)
  5. Rate limit exceeded (429)
  6. Valid request that succeeds (200)

Each scenario shows the HTTP status code, the error detail format,
and the `_parse_osrm_error` helper output.

Usage:
    uv run --package osrm-api-gateway-examples examples/src/routing/error_handling_demo.py

Requires:
    - OSRM API Gateway running at http://localhost:8000

Note: The rate limit test sends many requests rapidly and may require
      resetting the rate limiter between runs.
"""

import sys
import time

import httpx
from config import settings

API_BASE_URL = settings.OSRM_API_URL


def try_request(method: str, path: str, json_body: dict | None = None,
                label: str = "", timeout: float = 10.0) -> None:
    """Make a request and print the result."""
    url = f"{API_BASE_URL}{path}"
    print(f"\n  [{label}] {method} {url}")

    try:
        if method == "GET":
            resp = httpx.get(url, timeout=timeout)
        else:
            resp = httpx.post(url, json=json_body or {}, timeout=timeout)

        print(f"    Status: {resp.status_code}")
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = body.get("detail", body)
                if isinstance(detail, list):
                    for err in detail:
                        loc = ".".join(str(p) for p in err.get("loc", []))
                        msg = err.get("msg", "")
                        print(f"    Detail: [{loc}] {msg}")
                elif isinstance(detail, str):
                    if len(detail) > 120:
                        print(f"    Detail: {detail[:120]}...")
                    else:
                        print(f"    Detail: {detail}")
                else:
                    print(f"    Body: {body}")
        except Exception:
            print(f"    Body (text): {resp.text[:200]}")
    except httpx.ConnectError:
        print("    Status: — (Connection refused)")
        print(f"    Detail: Gateway unreachable at {url}")
    except httpx.TimeoutException:
        print("    Status: — (Timeout)")
        print(f"    Detail: Request timed out after {timeout}s")
    except Exception as e:
        print(f"    Status: — (Unexpected error: {type(e).__name__})")


def main():
    print("=" * 60)
    print("Error Handling Demo")
    print("=" * 60)

    # Check if gateway is up first
    try:
        httpx.get(f"{API_BASE_URL}/health", timeout=3.0)
    except httpx.ConnectError:
        print(f"\n❌ Gateway not reachable at {API_BASE_URL}")
        print("   Start it with: uvicorn app.main:app")
        sys.exit(1)

    # --- 1. Valid request (baseline) ---
    print("\n--- 1. Valid Request (Baseline) ---")
    try_request("POST", "/route", {
        "origin": {"longitude": -84.09, "latitude": 9.93},
        "destination": {"longitude": -84.08, "latitude": 9.94},
    }, label="Valid route")

    # --- 2. Invalid coordinate (longitude out of bounds) ---
    print("\n--- 2. Invalid Coordinate (longitude > 180) ---")
    try_request("POST", "/route", {
        "origin": {"longitude": 200, "latitude": 9.93},
        "destination": {"longitude": -84.08, "latitude": 9.94},
    }, label="Bad longitude")

    # --- 3. Missing required field (no origin) ---
    print("\n--- 3. Missing Required Field (no origin) ---")
    try_request("POST", "/route", {
        "destination": {"longitude": -84.08, "latitude": 9.94},
    }, label="Missing origin")

    # --- 4. Invalid profile ---
    print("\n--- 4. Invalid Profile ---")
    try_request("POST", "/route", {
        "origin": {"longitude": -84.09, "latitude": 9.93},
        "destination": {"longitude": -84.08, "latitude": 9.94},
        "profile": "flying",
    }, label="Profile='flying'")

    # --- 5. Empty coordinates list for matrix ---
    print("\n--- 5. Empty Coordinates (/matrix) ---")
    try_request("POST", "/matrix", {
        "coordinates": [],
    }, label="Empty matrix")

    # --- 6. VRP with missing depots ---
    print("\n--- 6. VRP Missing Depots ---")
    try_request("POST", "/vrp", {
        "stops": [{"longitude": -84.09, "latitude": 9.93}],
    }, label="No depots")

    # --- 7. Malformed nearest (number < 1) ---
    print("\n--- 7. Nearest with number=0 ---")
    try_request("POST", "/nearest", {
        "coordinate": {"longitude": -84.09, "latitude": 9.93},
        "number": 0,
    }, label="number=0")

    # --- 8. Rate limit demonstration ---
    print("\n--- 8. Rate Limit (rapid requests to /nearest) ---")
    nearest_payload = {
        "coordinate": {"longitude": -84.09, "latitude": 9.93},
        "number": 1,
    }
    for i in range(5):
        try:
            resp = httpx.post(
                f"{API_BASE_URL}/nearest",
                json=nearest_payload,
                timeout=5.0,
            )
            if resp.status_code == 429:
                print(f"    Attempt {i+1}: 429 Rate Limit Exceeded!")
                retry_after = resp.headers.get("retry-after", "?")
                print(f"      Retry-After: {retry_after}s")
                detail = resp.json().get("detail", "")
                print(f"      Detail: {detail}")
                break
            else:
                print(f"    Attempt {i+1}: {resp.status_code} (OK)")
        except Exception as e:
            print(f"    Attempt {i+1}: Error — {type(e).__name__}")
        time.sleep(0.05)

    # --- Summary of error response format ---
    print("\n" + "=" * 60)
    print("Error Response Format Summary")
    print("=" * 60)
    print("""
  HTTP 422 (Validation Error):
    {
      "detail": [
        {
          "loc": ["body", "origin"],
          "msg": "Field required",
          "type": "missing"
        }
      ]
    }

  HTTP 429 (Rate Limit):
    {
      "detail": "Rate limit exceeded: 600 per 1 minute"
    }
    Headers: retry-after, x-ratelimit-limit, x-ratelimit-remaining

  HTTP 500 (Internal / OSRM Error):
    {
      "detail": {
        "code": "InvalidValue",
        "message": "Coordinate value out of range"
      }
    }
    """)


if __name__ == "__main__":
    main()
