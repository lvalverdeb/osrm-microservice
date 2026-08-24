"""Acceptance test for the parity harness itself.

Two properties, both offline -- no routing engine, no network beyond loopback:

* **No false positives.** Running the corpus with the gateway on both sides must
  come back completely clean. A dirty self-diff means the harness is broken, and
  every verdict it later gives about a change is worthless.
* **No false negatives.** A deliberately perturbed candidate must be caught. A
  comparator that reports green on a broken gateway is the one failure mode of
  the whole design that is invisible from the outside.

This drove the FastAPI app in-process until that implementation was removed; it
now starts the gateway binary against the recorded fixtures. The properties
being checked are unchanged.
"""

from __future__ import annotations

import json

import httpx
from conftest_gateway import gateway, replay_engine, requires_binary

from parity.compare import Verdict
from parity.corpus import build_all
from parity.runner import run_corpus

CASES = 2
SEED = 20260823
ENDPOINTS = ("route", "matrix", "matrix-graph", "trip", "match", "nearest", "tile", "health")


def client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=30.0)


class PerturbingTransport(httpx.AsyncHTTPTransport):
    """Wraps the gateway and corrupts one field, to prove detection works."""

    def __init__(self, path: str, mutate) -> None:
        super().__init__()
        self._path = path
        self._mutate = mutate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        if request.url.path != self._path:
            return response
        await response.aread()
        body = self._mutate(json.loads(response.content))
        return httpx.Response(response.status_code, json=body,
                              headers={"content-type": "application/json"})


@requires_binary
async def test_self_diff_is_clean():
    """The gateway against itself must produce no differences at all."""
    with replay_engine() as engine, gateway(engine) as url:
        cases = build_all(SEED, CASES, ENDPOINTS)
        async with client(url) as reference, client(url) as candidate:
            results = await run_corpus(cases, reference, candidate, chunk_size=80)

    assert results, "the corpus produced no results"
    offenders = {r.endpoint: [str(d) for c in r.cases for d in c.diffs]
                 for r in results if r.verdict is not Verdict.OK}
    assert not offenders, f"self-diff was not clean: {offenders}"


@requires_binary
async def test_perturbed_candidate_is_detected():
    """A one-field change in a proxied body must fail, not pass."""
    def bump_distance(body):
        body["routes"][0]["distance"] += 100.0
        return body

    with replay_engine() as engine, gateway(engine) as url:
        cases = build_all(SEED, CASES, ("route",))
        async with client(url) as reference:
            perturbed = httpx.AsyncClient(
                transport=PerturbingTransport("/route", bump_distance),
                base_url=url, timeout=30.0)
            async with perturbed as candidate:
                results = await run_corpus(cases, reference, candidate, chunk_size=80)

    assert results[0].verdict is Verdict.FAIL
    paths = {d.path for c in results[0].cases for d in c.diffs}
    assert "$.routes[0].distance" in paths


@requires_binary
async def test_missing_field_is_detected():
    """Dropping a key is caught by the both-trees walk."""
    def drop_waypoints(body):
        body.pop("waypoints", None)
        return body

    with replay_engine() as engine, gateway(engine) as url:
        cases = build_all(SEED, 1, ("route",))
        async with client(url) as reference:
            perturbed = httpx.AsyncClient(
                transport=PerturbingTransport("/route", drop_waypoints),
                base_url=url, timeout=30.0)
            async with perturbed as candidate:
                results = await run_corpus(cases, reference, candidate, chunk_size=80)

    assert results[0].verdict is Verdict.FAIL
