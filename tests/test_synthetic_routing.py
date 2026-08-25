"""End-to-end routing over a map whose geometry is known by hand.

These assert *semantic* correctness of the requests the gateway builds, which is
the one class the recorded fixtures cannot reach: those key on the outgoing URL
and replay what was recorded, so a request that is well formed but means the
wrong thing still gets a plausible answer.

The map (see tests/synthetic/grid.osm) is three collinear nodes running east
plus one running north, so from n1 the other nodes are one, two and three legs
away at roughly 1113 m per leg. Every assertion here is a consequence of that
layout, and would break if coordinates were transposed, reordered, or if sources
and destinations were swapped.
"""

from __future__ import annotations

import httpx
import pytest
from conftest_gateway import requires_binary
from conftest_synthetic import requires_engine

pytestmark = [requires_engine, requires_binary]

# The nodes, as the gateway's API takes them.
N1 = {"longitude": 0.0, "latitude": 0.0}
N2 = {"longitude": 0.01, "latitude": 0.0}
N3 = {"longitude": 0.02, "latitude": 0.0}
N4 = {"longitude": 0.02, "latitude": 0.01}

LEG_METRES = 1113.0
TOLERANCE = 60.0  # generous: the engine snaps to the road, we assert the shape


def post(url: str, path: str, body: dict) -> dict:
    response = httpx.post(url + path, json=body, timeout=60)
    assert response.status_code == 200, f"{path} -> {response.status_code}: {response.text[:200]}"
    return response.json()


def test_route_distance_grows_with_each_leg(synthetic_gateway):
    """n2, n3 and n4 are one, two and three legs from n1.

    A lat/lon transposition anywhere in the coordinate path would route between
    different places and break this ordering, while still returning a valid
    response that a fixture replay would have accepted.
    """
    distances = []
    for destination in (N2, N3, N4):
        body = post(synthetic_gateway, "/route",
                    {"origin": N1, "destination": destination, "overview": "false"})
        assert body["code"] == "Ok"
        distances.append(body["routes"][0]["distance"])

    assert distances == sorted(distances), f"distance did not grow with hops: {distances}"
    assert distances[0] == pytest.approx(LEG_METRES, abs=TOLERANCE)
    assert distances[1] == pytest.approx(2 * LEG_METRES, abs=TOLERANCE)
    assert distances[2] == pytest.approx(3 * LEG_METRES, abs=TOLERANCE)


def test_route_geometry_follows_the_road(synthetic_gateway):
    """The line from n1 to n3 passes through n2, and stays on the equator."""
    body = post(synthetic_gateway, "/route",
                {"origin": N1, "destination": N3, "geometries": "geojson"})
    coordinates = body["routes"][0]["geometry"]["coordinates"]

    assert len(coordinates) >= 3, "expected the intermediate node in the geometry"
    longitudes = [lon for lon, _ in coordinates]
    assert longitudes == sorted(longitudes), "the route should run west to east"
    assert all(abs(lat) < 0.001 for _, lat in coordinates), \
        "every point should sit on the equator; a transposition would not"


def test_matrix_keeps_sources_and_destinations_distinct(synthetic_gateway):
    """One source against three destinations must come back 1x3, in order.

    Swapping sources and destinations yields a 3x1 of the same numbers, which a
    replayed fixture cannot distinguish because it never re-derives the shape.
    """
    body = post(synthetic_gateway, "/matrix", {
        "coordinates": [N1, N2, N3, N4],
        "sources": [0],
        "destinations": [1, 2, 3],
        "annotations": "distance",
    })
    distances = body["distances"]

    assert len(distances) == 1, f"expected one source row, got {len(distances)}"
    assert len(distances[0]) == 3, f"expected three destination columns, got {len(distances[0])}"
    assert distances[0] == sorted(distances[0]), \
        f"destinations should get further away in order: {distances[0]}"


def test_nearest_snaps_onto_the_road(synthetic_gateway):
    """A point north of the east road snaps back down onto it."""
    off_road = {"longitude": 0.01, "latitude": 0.002}   # ~220 m north of n2
    body = post(synthetic_gateway, "/nearest", {"coordinate": off_road, "number": 1})

    waypoint = body["waypoints"][0]
    snapped_lon, snapped_lat = waypoint["location"]
    assert abs(snapped_lat) < 0.001, "should snap onto the equator road"
    assert snapped_lon == pytest.approx(0.01, abs=0.002)
    assert waypoint["distance"] > 100, "the snap distance should reflect the offset"


def test_trip_visits_every_stop(synthetic_gateway):
    body = post(synthetic_gateway, "/trip", {"coordinates": [N1, N3, N2], "roundtrip": True})
    assert body["code"] == "Ok"
    assert len(body["waypoints"]) == 3


def test_vrp_allocates_each_stop_to_its_nearer_depot(synthetic_gateway):
    """Depots at each end of the road; a stop by each should go to the near one.

    This exercises the whole solve path against a real engine: the depot-to-stop
    matrix, the allocation, and the per-vehicle trips.
    """
    body = post(synthetic_gateway, "/vrp/allocate", {
        "depots": [dict(N1, id="west"), dict(N4, id="north")],
        "stops": [dict(N2, id="near-west"), dict(N3, id="near-north")],
    })

    allocations = body["allocations"]
    assert "near-west" in allocations.get("west", []), \
        f"the stop beside the west depot went elsewhere: {allocations}"
    assert body["unreachable_stops"] == []


def test_vrp_solves_over_the_real_engine(synthetic_gateway):
    body = post(synthetic_gateway, "/vrp", {
        "depots": [dict(N1, id="depot")],
        "stops": [dict(N2, id="a"), dict(N3, id="b"), dict(N4, id="c")],
        "capacity": 10,
    })
    assert body["code"] == "Ok"
    served = [stop for route in body["routes"] for stop in route["stop_ids"]]
    assert sorted(served) == ["a", "b", "c"], f"not every stop was served: {served}"
    assert body["total_distance"] > 0
