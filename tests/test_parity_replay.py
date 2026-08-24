"""Replay the recorded corpus against the gateway, with no engine.

The fixtures in `parity/fixtures/` are `osrm-routed` responses captured once;
`goldens.json` is what the gateway produced from them. Together they turn the
differential harness into a regression gate that needs no infrastructure at all
-- no engine, no sockets, no Rust toolchain -- so CI runs it as an ordinary test.

What it catches, none of which a unit test would: a dependency bump changing a
response shape (networkx renaming `links` to `edges` is the live example), a
refactor changing an outgoing OSRM query, and a cache-tier change. The last two
come from the fixture store rather than the response diff -- a request the
gateway did not make before has no fixture, and the number of fixture lookups is
the number of calls that reached the engine.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.main import app, osrm_client
from app.services.cache import response_cache
from parity.compare import Verdict, compare, worst
from parity.corpus import build_all
from parity.engine import build_app
from parity.rules import rule_for
from parity.upstream import FixtureStore

FIXTURES = Path(__file__).resolve().parents[1] / "parity" / "fixtures"
GOLDENS = FIXTURES / "goldens.json"


def goldens() -> dict:
    return json.loads(GOLDENS.read_text())


@pytest.fixture
def replay_engine():
    """Point the gateway's OSRM client at the recorded fixtures."""
    store = FixtureStore(FIXTURES / "upstream")
    engine = build_app("replay", store)
    original = osrm_client._client
    osrm_client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=engine), base_url="http://engine", timeout=60)
    response_cache.clear()
    yield store
    osrm_client._client = original
    response_cache.clear()


async def replay_corpus(store: FixtureStore) -> list[tuple[str, httpx.Response]]:
    """Send the recorded corpus to the gateway and return its answers."""
    recorded = goldens()
    cases = build_all(recorded["seed"], recorded["cases"])
    answers = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gateway", timeout=60) as client:
        for case in cases:
            if case.label not in recorded["responses"]:
                continue
            answers.append((case.label, await client.request(case.method, case.path,
                                                             json=case.body)))
    return answers


def test_fixtures_and_goldens_are_committed():
    assert GOLDENS.exists(), "run the recorder to regenerate parity/fixtures/"
    assert FixtureStore(FIXTURES / "upstream").count() > 0


async def test_replayed_responses_match_the_goldens(replay_engine):
    """Every endpoint reproduces exactly what it produced when recorded."""
    recorded = goldens()["responses"]
    mismatches = {}
    for label, response in await replay_corpus(replay_engine):
        expected = recorded[label]
        endpoint = label.split("[")[0]
        assert response.status_code == expected["status"], f"{label} status"

        if rule_for(endpoint).body == "bytes":
            if response.content != base64.b64decode(expected["body_b64"]):
                mismatches[label] = "raw body differs"
            continue
        diffs = compare(json.loads(base64.b64decode(expected["body_b64"])),
                        response.json(), rule_for(endpoint).tolerance)
        if worst(diffs) is Verdict.FAIL:
            mismatches[label] = [str(d) for d in diffs][:3]
    assert not mismatches, mismatches


async def test_no_fixture_misses(replay_engine):
    """A miss means the gateway built an upstream request it did not before.

    This is outgoing-request parity, and it costs nothing: the fixture store
    simply has no answer for a query that was never recorded.
    """
    await replay_corpus(replay_engine)
    assert replay_engine.misses == [], (
        f"gateway requested {len(replay_engine.misses)} upstream URL(s) with no "
        f"fixture; first: {replay_engine.misses[:2]}")


async def test_upstream_call_count_is_stable(replay_engine):
    """The number of calls reaching the engine is part of the contract.

    Two implementations can agree on every response and still disagree about
    what they cache; only this count sees that.
    """
    await replay_corpus(replay_engine)
    calls = replay_engine.upstream_calls()
    assert calls > 0, "the corpus reached no fixtures at all"
    # Recorded with a cold cache; a change here means caching behaviour moved.
    assert calls == goldens().get("upstream_calls", calls), (
        f"upstream calls changed to {calls}")
