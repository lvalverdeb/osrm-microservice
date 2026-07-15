"""
Infrastructure Observability Demo

Demonstrates two infrastructure endpoints:
  1. GET /health     — service health with OSRM backend probe
  2. GET /metrics    — Prometheus-formatted metrics

Also shows how to interpret the structured logging output format
configured by logging_config.py.

Usage:
    uv run examples/src/infra/health_and_metrics.py

Requires:
    - OSRM API Gateway running at http://localhost:8000
"""

import httpx
import os
import time

API_BASE_URL = os.environ.get("OSRM_API_URL", "http://localhost:8000")


def check_health() -> dict:
    """Poll /health and return parsed response."""
    url = f"{API_BASE_URL}/health"
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


def fetch_metrics() -> str:
    """Fetch raw Prometheus metrics from /metrics."""
    url = f"{API_BASE_URL}/metrics"
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return resp.text


def parse_metric_value(text: str, name: str) -> float:
    """Extract a single float value from Prometheus text output."""
    for line in text.splitlines():
        if line.startswith(name) and not line.startswith("#"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return float(parts[-1])
                except ValueError:
                    continue
    return 0.0


def main():
    print("=" * 60)
    print("Infrastructure Observability Demo")
    print("=" * 60)

    # --- Health Check ---
    print("\n--- GET /health ---")
    for i in range(3):
        health = check_health()
        print(f"  Attempt {i+1}: status={health['status']}, "
              f"osrm_backend={health['osrm_backend']}")
        time.sleep(1)

    if health["osrm_backend"] == "down":
        print("\n  ℹ️  OSRM backend is not running.")
        print("     The gateway reports 'degraded' but continues serving cached data.")
        print("     Start OSRM via: docker compose up -d osrm")

    # --- Metrics ---
    print("\n--- GET /metrics ---")
    metrics_text = fetch_metrics()

    lines = metrics_text.splitlines()
    print(f"  Raw metrics output: {len(lines)} lines")
    print("  First 3 lines:")
    for line in lines[:3]:
        print(f"    {line}")

    # Parse key metrics
    print("\n  --- Key Metrics ---")
    metric_names = [
        "http_request_duration_seconds_count",
        "http_request_duration_seconds_sum",
        "http_requests_total",
    ]
    for name in metric_names:
        val = parse_metric_value(metrics_text, name)
        if val > 0:
            print(f"    {name}: {val}")

    # Extract per-endpoint request counts
    print("\n  --- Per-Endpoint Request Counts ---")
    for line in lines:
        if "http_request_duration_seconds_count" in line and "{" in line:
            val_str = line.split()[-1]
            val = float(val_str)
            if val > 0:
                label = line.split("{")[1].split("}")[0]
                print(f"    {label}: {val:.0f} requests")

    # --- Structured Logging Example ---
    print("\n--- Structured Logging Format ---")
    print("  The gateway logs in this format:")
    print('    2026-06-25 12:34:56,789 [INFO] app.services.osrm_client: ...')
    print("  Configuration:")
    print("    - app/logging_config.py controls level and format")
    print("    - DEBUG=true in .env enables debug-level logging")
    print("    - All service modules use: logger = logging.getLogger(__name__)")

    # --- Cache Behavior ---
    print("\n--- Cache Behavior (Response Caching) ---")
    print("  Same OSRM requests are cached for 15 minutes (TTLCache).")
    print("  Cache key = endpoint + sorted parameter hash.")
    print("  First request  → cache miss → OSRM fetch")
    print("  Second request → cache hit  → instant response from memory")

    # Simulate: make the same route request twice to see caching
    print("\n  Demo: identical /route request twice")
    route_url = f"{API_BASE_URL}/route"
    payload = {
        "origin": {"longitude": -84.09, "latitude": 9.93},
        "destination": {"longitude": -84.08, "latitude": 9.94},
    }

    t1 = time.time()
    r1 = httpx.post(route_url, json=payload, timeout=10.0)
    t1_elapsed = time.time() - t1
    print(f"    Request 1: {r1.status_code} in {t1_elapsed:.3f}s")

    t2 = time.time()
    r2 = httpx.post(route_url, json=payload, timeout=10.0)
    t2_elapsed = time.time() - t2
    print(f"    Request 2: {r2.status_code} in {t2_elapsed:.3f}s")
    if t2_elapsed < t1_elapsed * 0.5:
        print("    ✅ Cached! Second request was significantly faster.")
    else:
        print("    ℹ️  Both requests similar — cache may have missed (different params?)")

    # --- Retry Behavior ---
    print("\n--- Retry Behavior (Transient Failure Handling) ---")
    print("  If OSRM returns 5xx or times out:")
    print("    - Retries up to 3 times with exponential backoff")
    print("    - Backoff: 1s → 2s → 4s (max 10s)")
    print("    - 4xx errors are NOT retried (client mistakes)")
    print("    - After 3 failures: 500 is returned to the client")


if __name__ == "__main__":
    main()
