"""Recorded `osrm-routed` responses, keyed by the request that produced them.

This is what lets the harness run without an engine. Recording once turns "you
need a routing engine" into "you needed one once", and it buys two things a
response diff cannot:

* **Outgoing-request parity, for free.** Replay answers only requests it has a
  fixture for. If a gateway builds a different upstream URL or a different
  parameter set, it gets a miss rather than a plausible answer -- which is the
  same property `tests/test_parity_baseline.py` provides for four endpoints,
  extended to all of them without hand-written assertions.
* **Cache-divergence detection.** The store counts how often each fixture is
  requested, so two implementations that agree on every response but disagree
  on what they cache are distinguishable. Cross-comparison alone is blind to
  that: both can be wrong identically.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "upstream"


def request_key(method: str, path: str, query: str) -> str:
    """Return the fixture key for one upstream request.

    Query parameters are sorted before hashing. The two gateways emit them in
    the same order today -- that was made true deliberately, so the upstream
    URLs match byte for byte -- but keying on a sorted form means a future
    ordering change costs a re-record rather than a wall of false misses.
    """
    params = sorted(parse_qsl(query, keep_blank_values=True))
    canonical = json.dumps([method.upper(), path, params], sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Fixture:
    """One recorded upstream response."""

    status: int
    content_type: str
    body: bytes
    method: str
    path: str
    query: str

    def to_json(self) -> dict:
        """Serialise, base64-encoding the body so tiles survive."""
        return {
            "status": self.status,
            "content_type": self.content_type,
            "body_b64": base64.b64encode(self.body).decode(),
            "request": {"method": self.method, "path": self.path, "query": self.query},
        }

    @classmethod
    def from_json(cls, data: dict) -> Fixture:
        return cls(
            status=data["status"],
            content_type=data["content_type"],
            body=base64.b64decode(data["body_b64"]),
            method=data["request"]["method"],
            path=data["request"]["path"],
            query=data["request"]["query"],
        )


class FixtureStore:
    """Reads and writes fixtures on disk, and counts what was asked for."""

    def __init__(self, directory: Path = DEFAULT_FIXTURE_DIR) -> None:
        self.directory = directory
        self.hits: Counter[str] = Counter()
        self.misses: list[tuple[str, str]] = []

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def save(self, fixture: Fixture) -> str:
        """Write one fixture and return its key."""
        key = request_key(fixture.method, fixture.path, fixture.query)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path_for(key).write_text(json.dumps(fixture.to_json(), indent=2))
        return key

    def load(self, method: str, path: str, query: str) -> Fixture | None:
        """Return the fixture for a request, recording the hit or the miss."""
        key = request_key(method, path, query)
        target = self.path_for(key)
        if not target.exists():
            self.misses.append((path, query))
            return None
        self.hits[key] += 1
        return Fixture.from_json(json.loads(target.read_text()))

    def count(self) -> int:
        """How many fixtures are stored."""
        if not self.directory.exists():
            return 0
        return len(list(self.directory.glob("*.json")))

    def upstream_calls(self) -> int:
        """Total fixture lookups served, i.e. calls that reached the engine.

        Comparing this between two implementations for the same corpus is how
        cache-tier divergence shows up: identical responses, different counts.
        """
        return sum(self.hits.values())

    def reset_counts(self) -> None:
        self.hits.clear()
        self.misses.clear()
