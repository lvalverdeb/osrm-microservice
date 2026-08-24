"""Acceptance test for the parity harness itself.

Two properties, both offline -- no engine, no sockets, no Rust:

* **No false positives.** Running the corpus with the Python gateway on both
  sides must come back completely clean. A dirty self-diff means the harness is
  broken, and every verdict it later gives about a port is worthless.
* **No false negatives.** A deliberately perturbed candidate must be caught.
  A comparator that reports green on a broken port is the one failure mode of
  the whole design that is invisible from the outside.

The engine is stubbed at `OSRMClient`'s public methods rather than at the HTTP
seam: what is under test here is the harness, so the gateway above the client is
what needs to be real.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.main import app, osrm_client
from app.services.cache import response_cache
from parity.compare import Verdict
from parity.corpus import build_all
from parity.runner import run_corpus

# Small: this is a harness check, not a coverage sweep.
CASES = 2
SEED = 4242
ENDPOINTS = ("route", "matrix", "matrix-graph", "trip", "match", "nearest",
             "tile", "vrp", "vrp-allocate", "health")

LINE = {"type": "LineString", "coordinates": [[-84.09, 9.93], [-84.08, 9.94]]}


def _matrix(request):
    """A deterministic rectangular matrix of the shape the request implies."""
    total = len(request.coordinates)
    sources = request.sources if request.sources else list(range(total))
    destinations = request.destinations if request.destinations else list(range(total))
    durations = [[60.0 + s * 10 + d for d in destinations] for s in sources]
    distances = [[600.0 + s * 100 + d * 10 for d in destinations] for s in sources]
    return {"code": "Ok", "durations": durations, "distances": distances}


def _trip(request):
    """A trip whose waypoint order is the input order, so reordering is a no-op."""
    return {
        "code": "Ok",
        "trips": [{"geometry": LINE, "distance": 100.0 * len(request.coordinates),
                   "duration": 200.0 * len(request.coordinates)}],
        "waypoints": [{"trips_index": 0, "waypoint_index": i}
                      for i in range(len(request.coordinates))],
    }


@pytest.fixture
def stubbed_engine(monkeypatch):
    """Replace every upstream call with a deterministic stand-in."""
    response_cache.clear()
    monkeypatch.setattr(osrm_client, "ping", lambda: _async(True))
    monkeypatch.setattr(osrm_client, "get_matrix", lambda request: _async(_matrix(request)))
    monkeypatch.setattr(osrm_client, "get_trip", lambda request: _async(_trip(request)))
    monkeypatch.setattr(osrm_client, "get_route", lambda coordinates, request: _async(
        {"code": "Ok", "routes": [{"geometry": LINE, "distance": 1234.5, "duration": 678.9}],
         "waypoints": []}))
    monkeypatch.setattr(osrm_client, "match_trace", lambda request: _async(
        {"code": "Ok", "matchings": [{"geometry": LINE, "distance": 55.5, "confidence": 0.9}],
         "tracepoints": []}))
    monkeypatch.setattr(osrm_client, "get_nearest", lambda request: _async(
        {"code": "Ok", "waypoints": [{"location": [-84.09, 9.93], "distance": 3.5}]}))
    monkeypatch.setattr(osrm_client, "get_tile",
                        lambda profile, z, x, y: _async(f"tile:{z}/{x}/{y}".encode()))
    yield
    response_cache.clear()


async def _async(value):
    """Wrap a plain value in a coroutine."""
    return value


def gateway_client() -> httpx.AsyncClient:
    """An in-process client for the Python gateway."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://gateway", timeout=30.0)


class PerturbingTransport(httpx.AsyncBaseTransport):
    """Wraps the gateway and corrupts one field, to prove detection works."""

    def __init__(self, inner: httpx.AsyncBaseTransport, path: str, mutate) -> None:
        self._inner = inner
        self._path = path
        self._mutate = mutate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if request.url.path != self._path:
            return response
        await response.aread()
        body = self._mutate(json.loads(response.content))
        return httpx.Response(response.status_code, json=body,
                              headers={"content-type": "application/json"})


async def test_self_diff_is_clean(stubbed_engine):
    """The Python gateway against itself must produce no differences at all."""
    cases = build_all(SEED, CASES, ENDPOINTS)
    async with gateway_client() as reference, gateway_client() as candidate:
        results = await run_corpus(cases, reference, candidate, chunk_size=80)

    assert results, "the corpus produced no results"
    offenders = {r.endpoint: [str(d) for c in r.cases for d in c.diffs]
                 for r in results if r.verdict is not Verdict.OK}
    assert not offenders, f"self-diff was not clean: {offenders}"


async def test_perturbed_candidate_is_detected(stubbed_engine):
    """A one-field change in a proxied body must fail, not pass."""
    def bump_distance(body):
        body["routes"][0]["distance"] += 100.0
        return body

    cases = build_all(SEED, CASES, ("route",))
    async with gateway_client() as reference:
        perturbed = httpx.AsyncClient(
            transport=PerturbingTransport(httpx.ASGITransport(app=app), "/route", bump_distance),
            base_url="http://gateway", timeout=30.0)
        async with perturbed as candidate:
            results = await run_corpus(cases, reference, candidate, chunk_size=80)

    assert results[0].verdict is Verdict.FAIL
    paths = {d.path for c in results[0].cases for d in c.diffs}
    assert "$.routes[0].distance" in paths


async def test_missing_field_is_detected(stubbed_engine):
    """Dropping a key is caught by the both-trees walk."""
    def drop_waypoints(body):
        body.pop("waypoints", None)
        return body

    cases = build_all(SEED, 1, ("route",))
    async with gateway_client() as reference:
        perturbed = httpx.AsyncClient(
            transport=PerturbingTransport(httpx.ASGITransport(app=app), "/route", drop_waypoints),
            base_url="http://gateway", timeout=30.0)
        async with perturbed as candidate:
            results = await run_corpus(cases, reference, candidate, chunk_size=80)

    assert results[0].verdict is Verdict.FAIL


async def test_identical_failures_are_marked_unproven(stubbed_engine, monkeypatch):
    """Both sides erroring the same way compares equal -- and proves nothing.

    Without this, a run where the engine is misconfigured reports a clean sweep.
    """
    async def boom(*args, **kwargs):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(osrm_client, "get_route", boom)
    cases = build_all(SEED, 1, ("route",))
    async with gateway_client() as reference, gateway_client() as candidate:
        results = await run_corpus(cases, reference, candidate, chunk_size=80)

    assert results[0].counts()["unproven"] == 1
    assert results[0].verdict is Verdict.ADVISORY
