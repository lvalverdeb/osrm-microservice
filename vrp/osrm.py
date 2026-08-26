"""Building a pinned `TravelMatrix` from the gateway — MTX-1…6, T-10.

The gateway owns the transport: talking to `osrm-routed`, retries, chunking,
caching. This module owns the *translation* into the domain model, which is a
different job and a fussier one. Three things it must not get wrong:

**Unreachable is not expensive** (MTX-5). OSRM reports a pair with no route as
`null`. Writing that as a large finite number — 10⁹ was this repository's own
choice in three examples before E-10 — makes it an arc a solver can use when
nothing better exists, and the plan comes back containing a leg no vehicle can
drive. It becomes `UNREACHABLE`, which `TravelMatrix` refuses to return from
`duration()` at all.

**Snapping is data quality** (MTX-4). Every coordinate is snapped to the road
network whether or not anyone looks, so the only question is whether the
distance is recorded. A stop 2 km from the nearest road still produces a
perfectly plausible matrix; what it does not produce is a plan that serves the
address anybody meant.

**The version pins the profile** (MTX-1, MTX-6). A matrix is per profile, and
INV-4 checks a plan against the matrix version it was built from. Two profiles
sharing a version would let a van plan be validated against bicycle travel.

Placement: **Python**. The transport half of this belongs in the Rust gateway
and is already there — `/matrix` and `/nearest` do the talking, with the retry
and chunking policy the gateway already owns. What is here is domain
translation: it constructs `TravelMatrix`, which is a Python type the solver and
verifier both consume, and its rules change with the optimisation model rather
than with transport behaviour. Moving it into the gateway would put the domain
model behind an HTTP boundary for no gain.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass

import httpx

from vrp.model import UNREACHABLE, TravelMatrix

# MTX-4: beyond this, a snap is reported as a data-quality problem. 100 m is
# generous for a delivery address and tight enough to catch a geocode that
# landed in the wrong street.
DEFAULT_SNAP_THRESHOLD_M = 100.0


class SnapWarning(UserWarning):
    """A location snapped further from the network than the threshold allows."""


@dataclass(frozen=True)
class Snap:
    """Where a location actually ended up on the road network. MTX-4."""

    location: tuple[float, float]        # (latitude, longitude) as given
    snapped: tuple[float, float]         # (latitude, longitude) on the network
    distance_m: float
    name: str = ""


def _coordinates(locations: list[tuple[float, float]]) -> list[dict]:
    """The gateway takes explicit keys, so a transposition cannot go unnoticed."""
    return [{"latitude": lat, "longitude": lon} for lat, lon in locations]


def matrix_version(locations: list[tuple[float, float]], profile: str,
                   extract_version: str = "unknown") -> str:
    """Content-addressed version. MTX-6.

    `profile` is the gateway's vocabulary -- "driving", "cycling", "walking" --
    not OSRM's raw profile filenames. The gateway rejects the latter, which is
    the right place for that to be caught.

    Hashes the locations, the profile and the extract, because a plan pins this
    string and INV-4 compares against it. The profile is in the hash *and* in
    the readable prefix: the hash makes it correct, the prefix makes a mismatch
    diagnosable without recomputing anything.
    """
    payload = json.dumps({"locations": locations, "profile": profile,
                          "extract": extract_version}, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"osrm:{profile}:{digest}"


def _snap_all(gateway: str, locations: list[tuple[float, float]], profile: str,
              threshold_m: float, timeout: float) -> list[Snap]:
    """One /nearest per location. MTX-4.

    Sequential on purpose: this runs once per matrix build, not per request, and
    the gateway already parallelises where it matters. Doing it concurrently
    here would trade a clear failure for a fast one.
    """
    snaps: list[Snap] = []
    for latitude, longitude in locations:
        response = httpx.post(f"{gateway}/nearest",
                              json={"coordinate": {"latitude": latitude,
                                                   "longitude": longitude},
                                    "number": 1, "profile": profile},
                              timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"/nearest returned {response.status_code}: "
                               f"{response.text[:200]}")
        waypoint = response.json()["waypoints"][0]
        snapped_lon, snapped_lat = waypoint["location"]
        snaps.append(Snap(location=(latitude, longitude),
                          snapped=(snapped_lat, snapped_lon),
                          distance_m=float(waypoint.get("distance", 0.0)),
                          name=waypoint.get("name", "")))

    far = [snap for snap in snaps if snap.distance_m > threshold_m]
    if far:
        warnings.warn(
            f"{len(far)} location(s) snapped more than {threshold_m:g} m from "
            f"the road network; furthest {max(s.distance_m for s in far):.0f} m "
            f"at {far[0].location}. A plan built on these serves the road, not "
            f"the address.", SnapWarning, stacklevel=3)
    return snaps


def _grid(raw: list[list], size: int, scale: float = 1.0) -> tuple[tuple[int, ...], ...]:
    """Whole units, with `null` preserved as the sentinel rather than a number."""
    return tuple(
        tuple(UNREACHABLE if raw[i][j] is None else round(raw[i][j] * scale)
              for j in range(size))
        for i in range(size)
    )


def build_matrix(gateway: str, locations: list[tuple[float, float]],
                 profile: str = "driving",
                 snap_threshold_m: float = DEFAULT_SNAP_THRESHOLD_M,
                 extract_version: str = "unknown",
                 timeout: float = 120.0) -> tuple[TravelMatrix, list[Snap]]:
    """Build a pinned matrix and the snap record behind it.

    Args:
        gateway: base URL of the OSRM API gateway.
        locations: `(latitude, longitude)` pairs, in the order the caller will
            index them by. Matrix indices follow this order exactly.
        profile: routing profile (MTX-1). Part of the version.
        snap_threshold_m: beyond this a `SnapWarning` is raised (MTX-4).
        extract_version: OSM extract identifier, folded into the version so a
            re-imported map invalidates plans pinned to the old one.
        timeout: per-request timeout in seconds.

    Returns:
        The matrix and one `Snap` per location, in the same order.

    Raises:
        RuntimeError: the gateway rejected the request.
        SnapWarning: not raised — issued as a warning, since a far snap is a
            data-quality problem the caller may legitimately accept.
    """
    if not locations:
        raise ValueError("cannot build a matrix over no locations")

    snaps = _snap_all(gateway, locations, profile, snap_threshold_m, timeout)

    response = httpx.post(f"{gateway}/matrix",
                          json={"coordinates": _coordinates(locations),
                                "annotations": "duration,distance",
                                "profile": profile},
                          timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"/matrix returned {response.status_code}: "
                           f"{response.text[:200]}")
    body = response.json()
    size = len(locations)

    matrix = TravelMatrix(
        version=matrix_version(locations, profile, extract_version),
        durations=_grid(body["durations"], size),
        distances=_grid(body["distances"], size),
    )
    return matrix, snaps
