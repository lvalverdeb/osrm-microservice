"""Tiling narrows cells; it must narrow coordinates too — `MTX-7`.

`MTX-7` splits an n x n matrix into blocks inside the cell cap, and that is
what `plan_tiles` does. But every tile was posted with the *whole* coordinate
list and only the index lists narrowed, and the gateway renders every
coordinate into the upstream path (`handlers.rs:matrix_call`), refusing over
`OSRM_MAX_URL_BYTES` before it contacts any engine. So the ceiling was the
location count, not the cell count, and tiling did not move it.

Measured against the compiled gateway on real corpus coordinates: 901
locations reached upstream, 1,182 were refused needing 24,884 bytes, and a
1,801-stop facility needed 37,388 against a 24,000 limit. That is an ordinary
hub in `vrp_problem_definition.md` §10 — the largest facility and the one its
worked example uses to demonstrate the capacity gap.

The failure mode is what makes this worth a regression test rather than a
note. `build_large_matrix` pre-fills every cell `UNREACHABLE` and treats a
failed tile as `NFR-04`'s degraded path, so all 361 tiles fail, a matrix comes
back with `degraded` set, and `diagnose.preflight` then reports every package
unreachable. Nothing raises. It looks like a run.
"""

from __future__ import annotations

import pytest

from vrp import matrix as m

CEILING_BYTES = 24_000          # OSRM_MAX_URL_BYTES, deploy/env/app.env
BYTES_PER_COORDINATE = 20.2     # measured on data/deliveries_cr.json


def locations(n: int) -> list[tuple[float, float]]:
    """Each location's longitude is its index, so a value identifies its cell."""
    return [(0.0, float(i)) for i in range(n)]


@pytest.fixture
def posted(monkeypatch):
    """Capture every /matrix body, and answer it the way OSRM would."""
    bodies = []

    class Response:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            coords = self._body["coordinates"]
            src = [coords[s] for s in self._body["sources"]]
            dst = [coords[d] for d in self._body["destinations"]]
            grid = [[1000 * s["longitude"] + d["longitude"] for d in dst]
                    for s in src]
            return {"durations": grid, "distances": grid}

    def post(url, json, timeout):
        bodies.append(json)
        return Response(json)

    monkeypatch.setattr(m.httpx, "post", post)
    return bodies


def build(n: int, posted, max_cells: int = 10_000):
    return m.build_large_matrix(
        "http://gateway", locations(n), max_cells=max_cells,
        snap=lambda *a, **k: [])


def test_a_tile_sends_only_the_coordinates_it_needs(posted):
    build(300, posted)

    assert posted, "no tile was fetched"
    for body in posted:
        needed = len(set(body["sources"]) | set(body["destinations"]))
        assert len(body["coordinates"]) == needed, (
            f"tile posted {len(body['coordinates'])} coordinates for "
            f"{needed} it addresses; the rest inflate the upstream URL")


def test_a_hub_sized_facility_stays_under_the_upstream_url_ceiling(posted):
    """1,801 stops is §10's hub. This is the case that was refused outright."""
    build(1801, posted)

    worst = max(len(body["coordinates"]) for body in posted)
    assert worst * BYTES_PER_COORDINATE < CEILING_BYTES, (
        f"worst tile carries {worst} coordinates ≈ "
        f"{worst * BYTES_PER_COORDINATE:,.0f} bytes, over the "
        f"{CEILING_BYTES:,}-byte upstream limit")


def test_renumbering_puts_each_answer_in_the_cell_it_belongs_to(posted):
    """The dangerous half of the fix.

    Sending a subset means the indices sent are no longer the caller's. Get
    that wrong and every tile still returns numbers, the matrix still builds,
    and every arc is quietly attributed to the wrong pair.
    """
    built, _ = build(250, posted)

    for i in (0, 99, 100, 150, 249):
        for j in (0, 1, 99, 100, 249):
            assert built.durations[i][j] == 1000 * i + j, (
                f"cell ({i},{j}) holds {built.durations[i][j]}, "
                f"expected {1000 * i + j}")


def test_a_matrix_small_enough_for_one_tile_is_unchanged(posted):
    """The single-tile path is the common case and must not regress."""
    built, _ = build(40, posted)

    assert len(posted) == 1
    assert built.durations[7][13] == 7013
    assert built.degraded is None
