"""E01 — How wrong is a straight line, and how asymmetric is a road network?

Two claims the papers make about why road routing is not geometry, measured on
the Costa Rica corpus rather than asserted:

* the **detour ratio**, road distance over great-circle distance, per ordered
  pair; and
* the **asymmetry**, |d(i,j) - d(j,i)| as a share of the pair's mean.

Both come from the same matrices, one drawn from the Greater Metropolitan Area
and one nationwide, because the two populations behave differently and quoting
a single blended number would hide that.

Writes `results/e01_detour.json` and `results/e01_asymmetry.json`.
"""

from __future__ import annotations

from typing import Any

from common import (
    client,
    coord,
    describe,
    haversine_m,
    load_deliveries,
    post,
    record,
    sample,
)

POINTS = 70
SEED = 20260902


def _matrix(http, rows: list[dict[str, Any]]) -> list[list[float | None]]:
    """Fetch the road-distance matrix for `rows`."""
    body = post(http, "/matrix", {
        "coordinates": [coord(r) for r in rows],
        "annotations": "duration,distance",
    })
    return body["distances"]


def _detours(rows, distances) -> tuple[list[float], dict[str, Any]]:
    """Detour ratio per ordered pair, plus the most extreme pair found."""
    ratios: list[float] = []
    worst = {"ratio": 0.0}
    for i, row in enumerate(rows):
        for j, other in enumerate(rows):
            if i == j:
                continue
            road = distances[i][j]
            straight = haversine_m(row, other)
            if road is None or straight < 100.0:
                continue          # below 100 m the ratio is dominated by snapping
            ratio = road / straight
            ratios.append(ratio)
            if ratio > worst["ratio"]:
                worst = {
                    "ratio": ratio,
                    "road_m": road,
                    "straight_m": straight,
                    "from": {"id": row["order_id"], **coord(row)},
                    "to": {"id": other["order_id"], **coord(other)},
                }
    return ratios, worst


def _asymmetries(rows, distances) -> tuple[list[float], dict[str, Any]]:
    """Relative asymmetry per unordered pair, plus the most extreme pair."""
    shares: list[float] = []
    worst = {"share": 0.0}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            there, back = distances[i][j], distances[j][i]
            if there is None or back is None:
                continue
            mean = (there + back) / 2
            if mean < 100.0:
                continue
            share = abs(there - back) / mean
            shares.append(share)
            if share > worst["share"]:
                worst = {
                    "share": share,
                    "there_m": there,
                    "back_m": back,
                    "from": {"id": rows[i]["order_id"], **coord(rows[i])},
                    "to": {"id": rows[j]["order_id"], **coord(rows[j])},
                }
    return shares, worst


def main() -> None:
    """Run both analyses over a GAM and a nationwide sample."""
    deliveries, _, meta = load_deliveries()
    gam = [d for d in deliveries if d.get("gam")]
    populations = {
        "gam": sample(gam, POINTS, SEED),
        "nationwide": sample(deliveries, POINTS, SEED + 1),
    }

    detour: dict[str, Any] = {"points_per_sample": POINTS, "seed": SEED,
                              "corpus": {"size": meta["count"]}}
    asymmetry: dict[str, Any] = dict(detour)

    with client() as http:
        for name, rows in populations.items():
            distances = _matrix(http, rows)
            ratios, worst_detour = _detours(rows, distances)
            shares, worst_asym = _asymmetries(rows, distances)
            detour[name] = {"ratio": describe(ratios), "worst": worst_detour}
            asymmetry[name] = {
                "relative": describe(shares),
                "worst": worst_asym,
                "share_above_1pct": sum(s > 0.01 for s in shares) / len(shares),
                "share_above_10pct": sum(s > 0.10 for s in shares) / len(shares),
            }

    print(record("e01_detour", detour))
    print(record("e01_asymmetry", asymmetry))


if __name__ == "__main__":
    main()
