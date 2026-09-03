"""The payload the other clustering examples post, built from the real corpus.

Writes `stress_test_payload.json`: the six depots the fleet actually runs from
and a share of real deliveries near each, in the shape `/vrp` expects.

It used to invent both. The depots were six hard-coded pairs of coordinates --
the same six, copied -- and the stops were `random.uniform` offsets around
them, unseeded, so every run produced a different file and the committed
payload changed whenever anybody executed an example. The corpus has the depots
and 50,000 road-snapped deliveries, so none of that needed inventing and none
of it needed committing.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/clustering/generate_payload.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "examples" / "src"))

import dataset

OUTPUT = PROJECT_ROOT / "examples" / "src" / "clustering" / "stress_test_payload.json"
STOPS = 50
CAPACITY = 35


def build(stops: int = STOPS) -> dict:
    """A `/vrp` payload over real depots and real deliveries.

    Args:
        stops: How many deliveries to take, shared across the depots.

    Returns:
        The payload: every depot, a share of the work near each, and a vehicle
        capacity. No `max_radius_km`, so clustering is global.
    """
    corpus = dataset.load()
    deliveries, depots = corpus.around_each_depot(stops)
    return {
        "depots": [{"latitude": d["latitude"], "longitude": d["longitude"]}
                   for d in depots],
        "stops": [{"id": d["product_id"], "latitude": d["latitude"],
                   "longitude": d["longitude"]} for d in deliveries],
        "capacity": CAPACITY,
    }


def main() -> int:
    payload = build()
    OUTPUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUTPUT.relative_to(PROJECT_ROOT)}: "
          f"{len(payload['depots'])} depots, {len(payload['stops'])} stops, "
          f"capacity {payload['capacity']}.")
    print("Deterministic: the same corpus gives the same file, so this is an "
          "artifact rather than something to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
