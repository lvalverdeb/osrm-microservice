"""Corpus determinism and stability.

Runs offline: no engine, no gateway, no Rust. These properties are what let a
recorded fixture set be trusted and appended to rather than re-recorded from
scratch every time someone changes a case count.
"""

from __future__ import annotations

import pytest

from loadtest.run import BUILDERS
from parity.corpus import ENDPOINTS, build, build_all


def test_same_seed_yields_identical_cases():
    assert build(7, "route", 5) == build(7, "route", 5)


def test_different_seeds_yield_different_cases():
    assert build(7, "route", 5) != build(8, "route", 5)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_corpus_is_append_only(endpoint):
    """Growing the case count must extend the corpus, not reshuffle it.

    This is the property that makes recorded fixtures durable, and the reason
    each endpoint draws from its own derived stream instead of the shared one
    `loadtest.run` uses.
    """
    assert build(7, endpoint, 5) == build(7, endpoint, 10)[:5]


def test_endpoints_are_independent():
    """One endpoint's case count must not perturb another's payloads."""
    route_alone = build_all(7, 3, endpoints=("route",))
    route_after_matrix = [c for c in build_all(7, 3, endpoints=("matrix", "route"))
                          if c.endpoint == "route"]
    assert route_alone == route_after_matrix


def test_every_builder_is_reachable():
    """`mixed` is a load-shaping construct and is correctly absent."""
    assert set(ENDPOINTS) == set(BUILDERS)
    assert "mixed" not in ENDPOINTS


def test_unknown_endpoint_is_rejected():
    with pytest.raises(KeyError, match="unknown endpoint"):
        build(7, "not-an-endpoint", 1)


def test_size_override_changes_payload_shape():
    default = build(7, "matrix", 1)[0]
    larger = build(7, "matrix", 1, size=60)[0]
    assert len(larger.body["coordinates"]) > len(default.body["coordinates"])


def test_cases_carry_a_stable_label():
    case = build(7, "route", 3)[2]
    assert case.label == "route[2]"
