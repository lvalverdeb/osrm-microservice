"""Slices of the Costa Rica delivery dataset — `data/deliveries_cr.json`.

Fifty thousand road-validated deliveries across six depots is a planning
corpus, not one request: a solve is capped at `VRP_MAX_STOPS` and a dense
matrix is quadratic besides. Every example that uses the dataset therefore
takes a slice, and until this module existed each one carried its own copy of
the slicing code.

**The selection strategy is the parameter, not an implementation detail.**
Which stops you take decides whether the demonstration demonstrates anything,
and the strategies here were each arrived at by watching an example fail to
make its point:

- `nearest` — a realistic day's work around one depot. The default.
- `spread` — the same catchment, walked rather than crowded. The twelve
  nearest deliveries sit inside a kilometre of each other, which makes driving
  free beside a vehicle's fixed cost and sends a solver to its penalty bound
  instead of an answer.
- `furthest` — a day with real driving in it. Hours-of-service only bites when
  the driving is long; a dense urban round never reaches 4.5 hours at the
  wheel, so a rules engine demonstrated on one never fires.
- `cluster_with_outliers` — a tight round plus the stops nobody wants to drive
  to, because a uniformly tight day has no decision in it.
- `around_each_depot` — a share of the work near every depot. Taking the
  nearest N to the *centroid* instead put every stop within one van's reach of
  a single depot, so the solver rightly used one vehicle and the multi-depot
  half of the problem went unexercised: the example ran, proved nothing, and
  looked like it had.

Two distance metrics appear below and the difference is deliberate. Ranking
near a depot uses squared degrees, which is cheap and monotone with distance
over a few kilometres; ranking the whole country uses great-circle metres,
because a degree of longitude and a degree of latitude are not the same length
and over 300 km that reorders the tail. Both are preserved as the examples
that depend on them were written and tuned.

The dataset is generated rather than committed — it is 12 MB and reproducible
from a seed — so `load` fails with the command that builds it rather than a
traceback. See `docs/dataset_prep.md`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("data/deliveries_cr.json")

#: How many deliveries `busiest_depot` weighs. The whole corpus would pick the
#: depot nearest the country's centre of mass every time, whatever the slice.
_DEPOT_SAMPLE = 400


def great_circle_metres(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Distance between two (latitude, longitude) pairs, in whole metres.

    Args:
        a: Origin as (latitude, longitude) in decimal degrees.
        b: Destination as (latitude, longitude) in decimal degrees.

    Returns:
        The great-circle distance, rounded to the nearest metre.
    """
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0]))
         * math.sin(dlon / 2) ** 2)
    return round(6_371_000 * 2 * math.asin(math.sqrt(h)))


def _square_degrees(delivery: dict[str, Any], depot: dict[str, Any]) -> float:
    """Squared degree distance from a delivery to a depot, for ranking only."""
    return ((delivery["latitude"] - depot["latitude"]) ** 2
            + (delivery["longitude"] - depot["longitude"]) ** 2)


@dataclass(frozen=True)
class Dataset:
    """A loaded delivery corpus, and the slices an example can take of it.

    Attributes:
        depots: Depot records, each with `name`, `latitude`, `longitude`.
        deliveries: Delivery records, each with `product_id`, `latitude`,
            `longitude`, `province`, `weight_kg`, `units`, `priority`,
            `service_minutes` and `category`.
        meta: Generation metadata — seed, counts, snapping bounds.
    """

    depots: list[dict[str, Any]]
    deliveries: list[dict[str, Any]]
    meta: dict[str, Any]

    def by_province(self, province: str | None) -> Dataset:
        """Restrict to one province, keeping every depot.

        Args:
            province: Province name, or None to keep the corpus whole.

        Returns:
            A dataset holding only that province's deliveries.

        Raises:
            SystemExit: If the province names nothing in the corpus, which is
                a typo rather than a condition worth a traceback.
        """
        if not province:
            return self
        kept = [d for d in self.deliveries if d["province"] == province]
        if not kept:
            raise SystemExit(f"no deliveries in province {province!r}; "
                             f"try one of {sorted(self.meta['provinces'])}")
        return Dataset(self.depots, kept, self.meta)

    def busiest_depot(self) -> dict[str, Any]:
        """The depot with the most of this dataset's work near it.

        Returns:
            The depot record closest to the bulk of the deliveries.
        """
        return min(self.depots,
                   key=lambda depot: sum(_square_degrees(d, depot)
                                         for d in self.deliveries[:_DEPOT_SAMPLE]))

    def nearest(self, stops: int, depot: dict[str, Any] | None = None
                ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """A realistic day's work: the closest deliveries to one depot.

        Args:
            stops: How many deliveries to take.
            depot: The depot to work from. Defaults to the first.

        Returns:
            The chosen deliveries, and the depot they were chosen around.
        """
        home = depot or self.depots[0]
        ranked = sorted(self.deliveries, key=lambda d: _square_degrees(d, home))
        return ranked[:stops], home

    def furthest(self, stops: int, depot: dict[str, Any] | None = None
                 ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """A day with real driving in it: the deliveries hardest to reach.

        Args:
            stops: How many deliveries to take.
            depot: The depot to measure from. Defaults to the first.

        Returns:
            The chosen deliveries, and the depot they were measured from.
        """
        home = depot or self.depots[0]
        origin = (home["latitude"], home["longitude"])
        ranked = sorted(self.deliveries, reverse=True,
                        key=lambda d: great_circle_metres(
                            origin, (d["latitude"], d["longitude"])))
        return ranked[:stops], home

    def cluster_with_outliers(self, stops: int, outliers: int,
                              depot: dict[str, Any] | None = None
                              ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """A tight round plus the few deliveries nobody wants to drive to.

        The shape is the point. A uniformly tight day has no decision in it:
        every stop is worth serving, and an objective's mode cannot show up as
        a difference. The outliers are what make the mode matter.

        Args:
            stops: Total deliveries to take, outliers included.
            outliers: How many of that total come from the far tail.
            depot: The depot to measure from. Defaults to `busiest_depot`.

        Returns:
            The chosen deliveries, and the depot they were measured from.
        """
        home = depot or self.busiest_depot()
        origin = (home["latitude"], home["longitude"])
        ranked = sorted(self.deliveries,
                        key=lambda d: great_circle_metres(
                            origin, (d["latitude"], d["longitude"])))
        near = ranked[:max(stops - outliers, 1)]
        far = ranked[-outliers:] if outliers else []
        return near + far, home

    def spread(self, stops: int, pool: int = 2_000,
               depot: dict[str, Any] | None = None
               ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """A round covering a depot's catchment, not the block outside it.

        `nearest` takes the tightest cluster it can find, which for this corpus
        means twelve stops inside a kilometre. That is a real delivery round,
        but it is the wrong one for any example whose point is that distance
        costs something: with every leg under a kilometre the driving is free
        beside a vehicle's fixed cost, and a solver asked to trade the two hits
        its penalty bound instead of answering.

        So this walks the catchment instead of the doorstep: rank the nearest
        `pool` deliveries, then take every `pool // stops`-th. The result spans
        the same area a van would actually cover in a day.

        Args:
            stops: How many deliveries to take.
            pool: How many of the nearest to spread the selection across.
            depot: The depot to work from. Defaults to the first.

        Returns:
            The chosen deliveries, and the depot they were chosen around.
        """
        home = depot or self.depots[0]
        ranked = sorted(self.deliveries, key=lambda d: _square_degrees(d, home))
        step = max(1, min(pool, len(ranked)) // max(stops, 1))
        return ranked[:step * stops:step][:stops], home

    def around_each_depot(self, stops: int
                          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """A share of the work near every depot, so assignment is a decision.

        Args:
            stops: Total deliveries to take, divided evenly across depots.

        Returns:
            The chosen deliveries, and every depot.
        """
        per_depot = max(1, stops // len(self.depots))
        chosen: list[dict[str, Any]] = []
        taken: set[str] = set()
        for depot in self.depots:
            ranked = sorted((d for d in self.deliveries
                             if d["product_id"] not in taken),
                            key=lambda d: _square_degrees(d, depot))
            for delivery in ranked[:per_depot]:
                chosen.append(delivery)
                taken.add(delivery["product_id"])
        return chosen, self.depots


def load(path: Path = DEFAULT_PATH) -> Dataset:
    """Read the delivery corpus, or say how to build it.

    Args:
        path: Where the dataset lives.

    Returns:
        The loaded dataset.

    Raises:
        SystemExit: If the file is absent. It is generated rather than
            committed, so the fix is a command, not a bug report.
    """
    if not path.exists():
        raise SystemExit(
            f"no dataset at {path}\n"
            "build it with:\n"
            "  uv run --package osrm-api-gateway-examples "
            "examples/src/fleet/generate_delivery_dataset.py\n"
            "see docs/dataset_prep.md")
    raw = json.loads(path.read_text())
    return Dataset(raw["depots"], raw["deliveries"], raw["meta"])
