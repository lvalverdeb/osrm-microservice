"""Tiling and pair-level caching for large matrices — MTX-7, MTX-10, T-11.

Two problems the gateway leaves to this layer, for structural reasons rather
than because nobody got to them.

**Tiling.** n² is the wall MTX-7 names: 5,000 locations is 25 million cells, and
the gateway refuses anything past `MATRIX_MAX_CELLS` with a 422 rather than
truncating. Tiles go over `sources`/`destinations`, which take index lists, so
one upload of the coordinates serves every tile.

**Pair caching.** The gateway's cache keys on the endpoint path plus a digest of
the params, and for `/table` the coordinates *are* the path. Change one stop and
the key changes, so the whole matrix is refetched. MTX-10 wants ≥90% pair reuse
on incremental days, and no request-keyed cache can give that at any hit rate:
the unit of reuse has to be the pair. Hence a second cache above the first,
which is duplication only if you squint — they key on different things and miss
on different things.

Coordinates are rounded before they become cache keys. Two floats that differ in
the last bit describe the same doorway, and a cache that treats them as
different pairs has a hit rate of approximately zero on real geocoded data.

Placement: **Python**. Tiling is matrix-shaped work driven by the solver's
needs, and the pair cache is keyed on domain coordinates. The gateway keeps what
it already owns -- per-request caching, retries, and the cell cap itself.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import httpx

from vrp.model import UNREACHABLE, TravelMatrix
from vrp.osrm import DEFAULT_SNAP_THRESHOLD_M, Snap, _snap_all, matrix_version

# The gateway's own default. Kept here as a default argument rather than read
# from the gateway, because a caller planning tiles offline has no gateway to
# ask -- and a tile plan that disagrees with the server is caught by the 422.
DEFAULT_MAX_CELLS = 10_000

# ~1 cm at the equator. Finer than any delivery address is known to, coarse
# enough that float noise cannot split one doorway into two cache entries.
COORDINATE_PRECISION = 7


@dataclass(frozen=True)
class Tile:
    """One sub-request: a block of sources against a block of destinations."""

    sources: range
    destinations: range

    @property
    def cells(self) -> int:
        return len(self.sources) * len(self.destinations)


def plan_tiles(size: int, max_cells: int = DEFAULT_MAX_CELLS) -> list[Tile]:
    """Split an `size` x `size` matrix into blocks within the cell cap. MTX-7.

    Square-ish blocks rather than whole rows: a row of a 5,000-location matrix
    is 5,000 cells, so a row-per-request scheme works only while the cap exceeds
    the location count, and fails on exactly the instances that need tiling.

    The tiles cover every cell exactly once. Gaps leave holes in the matrix, and
    overlaps pay for a round trip twice and invite two answers for one pair.
    """
    if size <= 0:
        raise ValueError("cannot tile a matrix of no locations")
    if max_cells <= 0:
        raise ValueError("max_cells must be positive")

    if size * size <= max_cells:
        return [Tile(range(size), range(size))]

    # Largest square block within the cap, at least 1x1 so progress is
    # guaranteed even when max_cells is smaller than one row.
    block = max(1, int(max_cells ** 0.5))
    return [
        Tile(range(i, min(i + block, size)), range(j, min(j + block, size)))
        for i in range(0, size, block)
        for j in range(0, size, block)
    ]


def _key(origin: tuple[float, float], destination: tuple[float, float],
         profile: str) -> tuple:
    """An ordered, rounded, profile-aware key. MTX-1, MTX-2, MTX-10."""
    return (round(origin[0], COORDINATE_PRECISION),
            round(origin[1], COORDINATE_PRECISION),
            round(destination[0], COORDINATE_PRECISION),
            round(destination[1], COORDINATE_PRECISION),
            profile)


class PairCache:
    """LRU cache of (origin, destination, profile) -> (duration, distance).

    LRU rather than FIFO because the access pattern is not uniform: depot rows
    are touched every single day and are exactly what a FIFO policy would evict
    after enough churn.

    In-memory only. MTX-10 also asks for a persistent store, which is `T-11`'s
    remaining half and wants a decision about where it lives -- Redis alongside
    the gateway's L2, or a local file. Reporting an in-memory hit rate honestly
    is better than pretending the persistence exists.
    """

    def __init__(self, max_entries: int = 5_000_000) -> None:
        self._entries: OrderedDict[tuple, tuple[int, int]] = OrderedDict()
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, origin: tuple[float, float], destination: tuple[float, float],
            profile: str) -> tuple[int, int] | None:
        """Travel for one pair, or None. Counts towards the hit rate."""
        key = _key(origin, destination, profile)
        if key in self._entries:
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]
        self.misses += 1
        return None

    def put(self, origin: tuple[float, float], destination: tuple[float, float],
            profile: str, duration: int, distance: int) -> None:
        key = _key(origin, destination, profile)
        self._entries[key] = (duration, distance)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def reset_stats(self) -> None:
        """Zero the counters without dropping the cache.

        Needed to measure one day's reuse against a cache warmed by the day
        before, which is the shape MTX-10's 90% is stated in.
        """
        self.hits = self.misses = 0

    @property
    def hit_rate(self) -> float:
        """Fraction of lookups served. Zero when nothing has been asked."""
        looked_up = self.hits + self.misses
        return self.hits / looked_up if looked_up else 0.0

    def __len__(self) -> int:
        return len(self._entries)


def _fetch_tile(gateway: str, coordinates: list[dict], tile: Tile,
                profile: str, timeout: float) -> dict:
    """One /matrix call restricted to a tile, over the full coordinate list."""
    response = httpx.post(
        f"{gateway}/matrix",
        json={"coordinates": coordinates,
              "sources": list(tile.sources),
              "destinations": list(tile.destinations),
              "annotations": "duration,distance",
              "profile": profile},
        timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"/matrix tile {tile.sources}x{tile.destinations} "
                           f"returned {response.status_code}: {response.text[:200]}")
    return response.json()


def build_large_matrix(gateway: str, locations: list[tuple[float, float]],
                       profile: str = "driving",
                       max_cells: int = DEFAULT_MAX_CELLS,
                       cache: PairCache | None = None,
                       snap_threshold_m: float = DEFAULT_SNAP_THRESHOLD_M,
                       extract_version: str = "unknown",
                       timeout: float = 120.0) -> tuple[TravelMatrix, list[Snap]]:
    """Build a matrix of any size by tiling, reusing cached pairs. MTX-7, MTX-10.

    The result is identical to an unchunked `build_matrix` over the same
    locations -- same cells, same version -- which is E-11's acceptance
    condition and the thing worth checking rather than assuming.

    Args:
        gateway: base URL of the OSRM API gateway.
        locations: `(latitude, longitude)` pairs; matrix indices follow this order.
        profile: routing profile (MTX-1), part of the version and the cache key.
        max_cells: the gateway's per-request cell cap.
        cache: pair cache to consult and fill. A fresh one is used if omitted,
            which means no reuse -- pass one in to get any.
        snap_threshold_m: beyond this a `SnapWarning` is raised (MTX-4).
        extract_version: OSM extract identifier, folded into the version.
        timeout: per-request timeout in seconds.

    Returns:
        The matrix and one `Snap` per location, in the same order.
    """
    if not locations:
        raise ValueError("cannot build a matrix over no locations")
    cache = cache if cache is not None else PairCache()
    size = len(locations)

    snaps = _snap_all(gateway, locations, profile, snap_threshold_m, timeout)
    coordinates = [{"latitude": lat, "longitude": lon} for lat, lon in locations]

    durations = [[UNREACHABLE] * size for _ in range(size)]
    distances = [[UNREACHABLE] * size for _ in range(size)]

    for tile in plan_tiles(size, max_cells):
        # Serve what the cache already knows, and only fetch a tile that still
        # has something missing. On a stable day most tiles never leave here.
        missing = False
        for i in tile.sources:
            for j in tile.destinations:
                cached = cache.get(locations[i], locations[j], profile)
                if cached is None:
                    missing = True
                else:
                    durations[i][j], distances[i][j] = cached
        if not missing:
            continue

        body = _fetch_tile(gateway, coordinates, tile, profile, timeout)
        for row, i in enumerate(tile.sources):
            for column, j in enumerate(tile.destinations):
                duration = body["durations"][row][column]
                distance = body["distances"][row][column]
                duration = UNREACHABLE if duration is None else round(duration)
                distance = UNREACHABLE if distance is None else round(distance)
                durations[i][j], distances[i][j] = duration, distance
                cache.put(locations[i], locations[j], profile, duration, distance)

    return TravelMatrix(
        version=matrix_version(locations, profile, extract_version),
        durations=tuple(tuple(row) for row in durations),
        distances=tuple(tuple(row) for row in distances),
    ), snaps
