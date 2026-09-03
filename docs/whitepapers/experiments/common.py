"""Shared fixtures for the whitepaper experiments.

Every figure quoted in `docs/whitepapers/` is produced by a script in this
directory and written to `results/` as JSON. Nothing is transcribed by hand:
the papers cite the script and the result file, so a reader can re-run any
number rather than trust it.

The gateway URL comes from `WHITEPAPER_GATEWAY`, defaulting to the instance the
papers were measured against. Sampling is seeded so a run is reproducible.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import random
import statistics
import time
from typing import Any

import httpx

GATEWAY = os.environ.get("WHITEPAPER_GATEWAY", "http://10.211.55.33:8000")
HERE = pathlib.Path(__file__).parent
RESULTS = HERE / "results"
DATASET = HERE.parents[2] / "data" / "deliveries_cr.json"

EARTH_RADIUS_M = 6_371_008.8


def client(timeout: float = 120.0) -> httpx.Client:
    """Return an HTTP client pointed at the gateway under test."""
    return httpx.Client(base_url=GATEWAY, timeout=timeout)


def post(http: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST `payload` to `path`, raising on any non-2xx status.

    Args:
        http: An open client from `client()`.
        path: Gateway path, e.g. `/matrix`.
        payload: JSON request body.

    Returns:
        The decoded response body.

    Raises:
        httpx.HTTPStatusError: If the gateway returned a non-2xx status.
    """
    response = http.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def load_deliveries() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Load the Costa Rica delivery corpus.

    Returns:
        A `(deliveries, depots, meta)` triple straight from the dataset.
    """
    raw = json.loads(DATASET.read_text())
    return raw["deliveries"], raw["depots"], raw["meta"]


def sample(items: list[Any], n: int, seed: int) -> list[Any]:
    """Draw `n` items reproducibly."""
    return random.Random(seed).sample(items, min(n, len(items)))


def coord(item: dict[str, Any]) -> dict[str, float]:
    """Project a dataset row onto the gateway's `Coordinate` shape."""
    return {"longitude": item["longitude"], "latitude": item["latitude"]}


def haversine_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Great-circle distance in metres between two dataset rows."""
    lat1, lon1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lon2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def describe(values: list[float]) -> dict[str, float]:
    """Summarise a sample with the quantiles the papers quote."""
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "p50": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "p90": ordered[int(0.90 * (len(ordered) - 1))],
        "p99": ordered[int(0.99 * (len(ordered) - 1))],
        "max": ordered[-1],
    }


def timed(fn, *args, **kwargs) -> tuple[Any, float]:
    """Call `fn`, returning its result and the elapsed wall-clock milliseconds."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - start) * 1000.0


def record(name: str, payload: dict[str, Any]) -> pathlib.Path:
    """Write an experiment result to `results/<name>.json` and return the path."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    payload = {"gateway": GATEWAY, **payload}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path
