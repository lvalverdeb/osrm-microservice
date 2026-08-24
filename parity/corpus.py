"""Seeded request corpus, built from the load generator's payload factories.

The builders are imported from `loadtest.run` rather than copied, so a change
to `build_route` moves the load corpus and the differential corpus together and
the two tools cannot silently disagree about what a request looks like.

One behaviour is deliberately *not* inherited. `loadtest.run` draws every
payload from a single `Random(seed)`, so the sequence for one endpoint depends
on how many requests the previous endpoint drew. That is right for a mixed load
run and wrong for a golden corpus: raising `--cases` for `/route` would reshuffle
every recorded `/vrp` case and invalidate the whole fixture set. Each endpoint
here gets its own stream derived from `(seed, endpoint)`, which makes the corpus
append-only -- `cases=10` extends `cases=5` rather than replacing it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from loadtest.run import BUILDERS, DEFAULT_SIZE, build_request

# `mixed` is a load-shaping construct, not an endpoint, so it has no place in a
# corpus that reports per-endpoint verdicts.
ENDPOINTS: tuple[str, ...] = tuple(sorted(BUILDERS))

# Prometheus text carries process-level and scrape-time-dependent series, so
# comparing it across implementations is noise rather than signal. The metrics
# contract needs its own test -- scrape, issue N requests, scrape, assert the
# delta -- not a response diff. Pass `--endpoints metrics` to include it anyway.
DEFAULT_ENDPOINTS: tuple[str, ...] = tuple(e for e in ENDPOINTS if e != "metrics")


@dataclass(frozen=True)
class Case:
    """One request to send to both gateways."""

    endpoint: str
    index: int
    method: str
    path: str
    body: dict[str, Any] | None

    @property
    def label(self) -> str:
        """Stable identifier used in reports and diff filenames."""
        return f"{self.endpoint}[{self.index}]"


def stream(seed: int | str, endpoint: str) -> random.Random:
    """Return the payload stream for one endpoint.

    Deriving from both the seed and the endpoint name is what keeps each
    endpoint's sequence independent of every other's.
    """
    return random.Random(f"{seed}:{endpoint}")


def build(seed: int | str, endpoint: str, cases: int, size: int | None = None) -> list[Case]:
    """Build `cases` requests for one endpoint.

    Args:
        seed: Corpus seed; the same seed always yields the same requests.
        endpoint: Key of `loadtest.run.BUILDERS`.
        cases: How many requests to generate.
        size: Payload size (waypoints, coordinates, stops or zoom). Defaults to
            the endpoint's `DEFAULT_SIZE`.

    Returns:
        The requests, in a stable order.

    Raises:
        KeyError: If `endpoint` is not a known scenario.
    """
    if endpoint not in BUILDERS:
        raise KeyError(f"unknown endpoint {endpoint!r}; known: {', '.join(ENDPOINTS)}")
    effective_size = DEFAULT_SIZE[endpoint] if size is None else size
    rng = stream(seed, endpoint)
    built = []
    for index in range(cases):
        method, path, body = build_request(rng, endpoint, effective_size)
        built.append(Case(endpoint, index, method, path, body))
    return built


def build_all(seed: int | str, cases: int, endpoints: tuple[str, ...] = ENDPOINTS,
              sizes: dict[str, int] | None = None) -> list[Case]:
    """Build the corpus for several endpoints.

    Args:
        seed: Corpus seed.
        cases: Requests per endpoint.
        endpoints: Which endpoints to cover.
        sizes: Per-endpoint size overrides.

    Returns:
        Every case, grouped by endpoint in the given order.
    """
    overrides = sizes or {}
    return [case for endpoint in endpoints
            for case in build(seed, endpoint, cases, overrides.get(endpoint))]
