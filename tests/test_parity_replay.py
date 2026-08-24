"""Replay the recorded corpus against the gateway, with no engine.

The fixtures in `parity/fixtures/` are `osrm-routed` responses captured once;
`goldens.json` is what the gateway produced from them. Together they make a
regression gate that needs no infrastructure -- no routing engine, no network
beyond loopback -- so a response shape cannot change without a test noticing.

Two properties come from the fixture store rather than the response diff, and
neither is visible to a comparison of bodies alone:

* **Outgoing-request parity.** Replay answers only what it recorded, so a
  gateway that builds a different upstream URL gets a 404 naming that URL rather
  than a plausible answer.
* **Cache behaviour.** The store counts fixture lookups, so a change in what the
  gateway caches shows up as a different number of calls reaching the engine.
"""

from __future__ import annotations

import base64
import json

import httpx
from conftest_gateway import FIXTURES, gateway, replay_engine, requires_binary

from parity.compare import Verdict, compare, worst
from parity.corpus import DEFAULT_ENDPOINTS, build_all
from parity.rules import rule_for
from parity.upstream import FixtureStore

GOLDENS = FIXTURES / "goldens.json"


def goldens() -> dict:
    return json.loads(GOLDENS.read_text())


async def replay_corpus(url: str) -> list[tuple[str, httpx.Response]]:
    """Send the recorded corpus to the gateway and return its answers."""
    recorded = goldens()
    answers = []
    async with httpx.AsyncClient(base_url=url, timeout=60.0) as client:
        for case in build_all(recorded["seed"], recorded["cases"], DEFAULT_ENDPOINTS):
            if case.label not in recorded["responses"]:
                continue
            answers.append((case.label,
                            await client.request(case.method, case.path, json=case.body)))
    return answers


def test_fixtures_and_goldens_are_committed():
    assert GOLDENS.exists(), "run the recorder to regenerate parity/fixtures/"
    assert FixtureStore(FIXTURES / "upstream").count() > 0


@requires_binary
async def test_replayed_responses_match_the_goldens():
    """Every endpoint reproduces exactly what it produced when recorded."""
    recorded = goldens()["responses"]
    mismatches = {}
    with replay_engine() as engine, gateway(engine) as url:
        answers = await replay_corpus(url)

    for label, response in answers:
        expected = recorded[label]
        endpoint = label.split("[")[0]
        assert response.status_code == expected["status"], f"{label} status"

        if not expected["content_type"].endswith("json"):
            if response.content != base64.b64decode(expected["body_b64"]):
                mismatches[label] = f"raw body differs ({expected['content_type']})"
            continue
        diffs = compare(json.loads(base64.b64decode(expected["body_b64"])),
                        response.json(), rule_for(endpoint).tolerance)
        if worst(diffs) is Verdict.FAIL:
            mismatches[label] = [str(d) for d in diffs][:3]
    assert not mismatches, mismatches


@requires_binary
async def test_no_fixture_misses():
    """A miss means the gateway built an upstream request it did not before.

    This is outgoing-request parity, and it costs nothing: the fixture store
    simply has no answer for a query that was never recorded. The replay engine
    reports one as a 404 naming the URL, so the gateway surfaces it rather than
    retrying it into a timeout.
    """
    misses = []
    with replay_engine() as engine, gateway(engine) as url:
        async with httpx.AsyncClient(base_url=url, timeout=60.0) as client:
            recorded = goldens()
            for case in build_all(recorded["seed"], recorded["cases"], DEFAULT_ENDPOINTS):
                if case.label not in recorded["responses"]:
                    continue
                response = await client.request(case.method, case.path, json=case.body)
                # A fixture miss surfaces as an upstream 404 passed through.
                if response.status_code == 404:
                    misses.append(case.label)
    assert not misses, (
        f"the gateway requested upstream URLs with no fixture, for: {misses[:3]}")
