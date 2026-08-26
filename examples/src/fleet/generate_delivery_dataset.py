"""Generate a planned-delivery dataset for Costa Rica, snapped to the road network.

Produces 50,000 delivery records spread across the seven provinces, weighted
towards the Greater Metropolitan Area, every one of them reachable by road.

Road-reachability is not assumed. Each candidate point is snapped through OSRM's
`/nearest`, and a point whose nearest road is further away than
`--max-snap-metres` is discarded rather than shipped with a coordinate no
vehicle can reach. The snapped coordinate is what gets written, so a consumer
routing to it never pays the snap again.

The depots and province hubs are the ones already used by
`clustering/run_clustering_workflow.py`, so a plan built from this dataset can be
compared against that example's output directly.

Usage:
    # against a running engine (see docs/dataset_prep.md for how to start one)
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/generate_delivery_dataset.py --engine http://127.0.0.1:5000

    # smaller sample, different spread
    ... --count 5000 --gam-share 0.55 --seed 7

Specification: docs/dataset_prep.md
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

import httpx

# --- Depots -----------------------------------------------------------------
# Verbatim from clustering/run_clustering_workflow.py so the two are comparable.
WAREHOUSES = [
    {"name": "Guadalupe (San Jose)", "latitude": 9.9472, "longitude": -84.0531},
    {"name": "Grecia (Alajuela)", "latitude": 10.0734, "longitude": -84.3121},
    {"name": "Guapiles (Limon)", "latitude": 10.2128, "longitude": -83.7847},
    {"name": "San Carlos (Alajuela North)", "latitude": 10.3228, "longitude": -84.4253},
    {"name": "Liberia (Guanacaste)", "latitude": 10.6333, "longitude": -85.5333},
    {"name": "Perez Zeledon (San Jose South)", "latitude": 9.3734, "longitude": -83.7029},
]

# --- Destination hubs -------------------------------------------------------
# `valle_central` marks the Greater Metropolitan Area, which is where the
# population and therefore the deliveries actually concentrate.
PROVINCES_HUBS = {
    "San Jose": [
        {"name": "San Jose Central", "latitude": 9.9333, "longitude": -84.0833, "valle_central": True},
        {"name": "Escazu", "latitude": 9.9200, "longitude": -84.1400, "valle_central": True},
        {"name": "Desamparados", "latitude": 9.8900, "longitude": -84.0600, "valle_central": True},
        {"name": "Santa Ana", "latitude": 9.9300, "longitude": -84.1800, "valle_central": True},
        {"name": "Perez Zeledon", "latitude": 9.3734, "longitude": -83.7029, "valle_central": False},
        {"name": "Puriscal", "latitude": 9.8500, "longitude": -84.3200, "valle_central": False},
    ],
    "Alajuela": [
        {"name": "Alajuela Central", "latitude": 10.0163, "longitude": -84.2139, "valle_central": True},
        {"name": "Grecia", "latitude": 10.0734, "longitude": -84.3121, "valle_central": True},
        {"name": "San Ramon", "latitude": 10.0800, "longitude": -84.4700, "valle_central": True},
        {"name": "San Carlos", "latitude": 10.3228, "longitude": -84.4253, "valle_central": False},
        {"name": "Upala", "latitude": 10.8833, "longitude": -85.0167, "valle_central": False},
    ],
    "Cartago": [
        {"name": "Cartago Central", "latitude": 9.8644, "longitude": -83.9194, "valle_central": True},
        {"name": "Paraiso", "latitude": 9.8378, "longitude": -83.8656, "valle_central": True},
        {"name": "La Union", "latitude": 9.9100, "longitude": -83.9800, "valle_central": True},
        {"name": "Turrialba", "latitude": 9.9048, "longitude": -83.6841, "valle_central": False},
    ],
    "Heredia": [
        {"name": "Heredia Central", "latitude": 9.9982, "longitude": -84.1167, "valle_central": True},
        {"name": "San Rafael", "latitude": 10.0100, "longitude": -84.1000, "valle_central": True},
        {"name": "Belen", "latitude": 9.9833, "longitude": -84.1833, "valle_central": True},
        {"name": "Sarapiqui", "latitude": 10.4503, "longitude": -84.0089, "valle_central": False},
    ],
    "Guanacaste": [
        {"name": "Liberia", "latitude": 10.6350, "longitude": -85.4407, "valle_central": False},
        {"name": "Canas", "latitude": 10.4310, "longitude": -85.0931, "valle_central": False},
        {"name": "Nicoya", "latitude": 10.1500, "longitude": -85.4500, "valle_central": False},
        {"name": "Santa Cruz", "latitude": 10.2611, "longitude": -85.5847, "valle_central": False},
    ],
    "Puntarenas": [
        {"name": "Puntarenas Central", "latitude": 9.9763, "longitude": -84.8384, "valle_central": False},
        {"name": "Esparza", "latitude": 9.9912, "longitude": -84.6647, "valle_central": False},
        {"name": "Jaco", "latitude": 9.6144, "longitude": -84.6289, "valle_central": False},
        {"name": "Quepos", "latitude": 9.4300, "longitude": -84.1600, "valle_central": False},
    ],
    "Limon": [
        {"name": "Limon Centre", "latitude": 9.9913, "longitude": -83.0415, "valle_central": False},
        {"name": "Guapiles", "latitude": 10.2128, "longitude": -83.7847, "valle_central": False},
        {"name": "Siquirres", "latitude": 10.1000, "longitude": -83.5000, "valle_central": False},
        {"name": "Bribri", "latitude": 9.6200, "longitude": -82.8500, "valle_central": False},
    ],
}

# Product mix. Weight drives the capacity dimension; the categories exist so a
# consumer has something to group and filter by.
CATEGORIES = [
    ("Groceries", 0.30, (0.5, 12.0)),
    ("Beverages", 0.15, (1.0, 20.0)),
    ("Household", 0.15, (0.3, 8.0)),
    ("Electronics", 0.10, (0.2, 6.0)),
    ("Pharmacy", 0.10, (0.05, 2.0)),
    ("Apparel", 0.10, (0.1, 3.0)),
    ("Hardware", 0.10, (1.0, 30.0)),
]

PRIORITIES = [("standard", 0.70), ("express", 0.20), ("scheduled", 0.10)]

# Spread in degrees around a hub. The GAM is dense and built up, so deliveries
# sit close to their hub; outside it a "hub" stands for a whole canton.
SPREAD_GAM_DEG = 0.045      # ~5 km
SPREAD_RURAL_DEG = 0.13     # ~14 km

EARTH_RADIUS_M = 6_371_000.0


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance, used to measure how far a point moved when snapped."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def weighted_choice(rng: random.Random, options: list[tuple]) -> tuple:
    """Pick from (value, weight, ...) tuples."""
    total = sum(o[1] for o in options)
    roll = rng.uniform(0, total)
    upto = 0.0
    for option in options:
        upto += option[1]
        if roll <= upto:
            return option
    return options[-1]


def split_hubs() -> tuple[list[tuple[dict, str]], list[tuple[dict, str]]]:
    """GAM hubs and the rest, each paired with its province."""
    gam, rural = [], []
    for province, hubs in PROVINCES_HUBS.items():
        for hub in hubs:
            (gam if hub["valle_central"] else rural).append((hub, province))
    return gam, rural


def snap_batch(client: httpx.Client, engine: str, points: list[tuple[float, float]],
               max_snap_m: float) -> list[tuple[float, float] | None]:
    """Snap each point to the road network, or None if it is too far from one.

    One `/nearest` call per point: it takes a single coordinate by definition,
    so there is no batch form to use.
    """
    out: list[tuple[float, float] | None] = []
    for lat, lon in points:
        try:
            response = client.get(f"{engine}/nearest/v1/driving/{lon:.6f},{lat:.6f}",
                                  params={"number": 1}, timeout=30)
            data = response.json()
            if data.get("code") != "Ok" or not data.get("waypoints"):
                out.append(None)
                continue
            snapped_lon, snapped_lat = data["waypoints"][0]["location"]
            moved = haversine_metres(lat, lon, snapped_lat, snapped_lon)
            out.append((snapped_lat, snapped_lon) if moved <= max_snap_m else None)
        except Exception:
            out.append(None)
    return out


def generate(count: int, seed: int, gam_share: float, engine: str | None,
             max_snap_m: float) -> dict:
    rng = random.Random(seed)
    gam_hubs, rural_hubs = split_hubs()

    # Quotas are filled against *accepted* records, not candidates. A rural
    # point is far likelier to be rejected for having no road near it, so
    # weighting the draw alone produced a 13-point skew towards the GAM against
    # what was asked for. Filling each quota separately makes the requested
    # share the share you actually get.
    target_gam = round(count * gam_share)
    quotas = {True: target_gam, False: count - target_gam}
    accepted = {True: 0, False: 0}

    records: list[dict] = []
    rejected = {True: 0, False: 0}
    order_seq = 0
    client = httpx.Client() if engine else None

    # Generated in batches so a rejected point can be replaced without
    # renumbering everything after it.
    while len(records) < count:
        # Draw from whichever class still owes records.
        outstanding = [k for k in (True, False) if accepted[k] < quotas[k]]
        batch = min(max(count - len(records), 1), 500)
        drafts = []
        for _ in range(batch):
            want_gam = rng.choice(outstanding)
            hub, province = rng.choice(gam_hubs if want_gam else rural_hubs)
            spread = SPREAD_GAM_DEG if hub["valle_central"] else SPREAD_RURAL_DEG
            # Gaussian, so density falls off from the hub rather than filling a
            # square uniformly; longitude is scaled so the spread is circular on
            # the ground rather than stretched by the projection.
            lat = hub["latitude"] + rng.gauss(0, spread / 2)
            lon = hub["longitude"] + rng.gauss(0, spread / 2) / math.cos(math.radians(hub["latitude"]))
            drafts.append((lat, lon, hub, province))

        if client:
            snapped = snap_batch(client, engine, [(d[0], d[1]) for d in drafts], max_snap_m)
        else:
            snapped = [(d[0], d[1]) for d in drafts]

        for (lat, lon, hub, province), result in zip(drafts, snapped):
            klass = hub["valle_central"]
            if result is None:
                rejected[klass] += 1
                continue
            # The batch may overshoot a quota that filled mid-batch; drop the
            # surplus rather than let it distort the requested share.
            if accepted[klass] >= quotas[klass] or len(records) >= count:
                continue
            snapped_lat, snapped_lon = result
            category, _weight, (wmin, wmax) = weighted_choice(rng, CATEGORIES)
            priority, _p = weighted_choice(rng, PRIORITIES)
            order_seq += 1
            accepted[klass] += 1
            records.append({
                "product_id": f"CR-{seed}-{order_seq:06d}",
                "order_id": f"ORD-{order_seq:06d}",
                "province": province,
                "hub": hub["name"],
                "gam": hub["valle_central"],
                "latitude": round(snapped_lat, 6),
                "longitude": round(snapped_lon, 6),
                "category": category,
                "weight_kg": round(rng.uniform(wmin, wmax), 2),
                "units": rng.randint(1, 6),
                "priority": priority,
                "service_minutes": rng.choice([3, 4, 5, 6, 8, 10, 12]),
            })

    if client:
        client.close()

    return {
        "meta": {
            "count": len(records),
            "seed": seed,
            "gam_share_requested": gam_share,
            "gam_share_actual": round(sum(r["gam"] for r in records) / max(len(records), 1), 4),
            "snapped_to_road_network": bool(engine),
            "max_snap_metres": max_snap_m if engine else None,
            "rejected_unreachable": rejected[True] + rejected[False],
            "rejected_gam": rejected[True],
            "rejected_rural": rejected[False],
            "provinces": sorted({r["province"] for r in records}),
            "spec": "docs/dataset_prep.md",
        },
        "depots": WAREHOUSES,
        "deliveries": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=20260825,
                        help="same seed, same dataset")
    parser.add_argument("--gam-share", type=float, default=0.72,
                        help="share of deliveries in the Greater Metropolitan Area")
    parser.add_argument("--engine", default=None,
                        help="OSRM base URL. Without it, points are NOT snapped "
                             "and road-reachability is not guaranteed")
    parser.add_argument("--max-snap-metres", type=float, default=250.0)
    parser.add_argument("--out", default="data/deliveries_cr.json")
    args = parser.parse_args()

    if not args.engine:
        print("WARNING: no --engine given. Points will not be snapped to the road\n"
              "         network, so some will not be reachable by road.\n",
              file=sys.stderr)

    dataset = generate(args.count, args.seed, args.gam_share,
                       args.engine, args.max_snap_metres)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataset, indent=None, separators=(",", ":")))

    meta = dataset["meta"]
    print(f"wrote {meta['count']:,} deliveries to {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"  GAM share  : {meta['gam_share_actual']:.1%} (requested {meta['gam_share_requested']:.0%})")
    print(f"  provinces  : {len(meta['provinces'])}")
    print(f"  snapped    : {meta['snapped_to_road_network']}")
    if meta["snapped_to_road_network"]:
        print(f"  discarded  : {meta['rejected_unreachable']:,} points with no road "
              f"within {meta['max_snap_metres']:.0f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
