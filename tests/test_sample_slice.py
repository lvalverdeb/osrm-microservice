"""The committed slice has to answer like the corpus it stands in for.

`examples/data/deliveries_sample.json` is what the examples read on a fresh
clone and in CI. Its whole value is that it is *not* a sample: every selection
in `build_sample_slice.MANIFEST` returns the same deliveries it would return
from the full 50,000-delivery corpus, so an example prints the same numbers
either way.

Nothing about that is self-evident from the file, and it breaks quietly. A
delivery dropped from the ranked prefix shifts every position below it, so an
example keeps running and starts describing a different day. These tests are
the only thing standing between that and a green suite.

Most of them need the full corpus and so are local-only; the last one does not,
and is what CI can still say about the artifact it ships.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "examples" / "src"))
sys.path.insert(0, str(REPO / "examples" / "tools"))

import build_sample_slice as builder
import dataset

needs_corpus = pytest.mark.skipif(
    not builder.FULL_PATH.exists(),
    reason=f"no full corpus at {builder.FULL_PATH}; build it with "
           "examples/src/vrp/generate_delivery_dataset.py")


@pytest.fixture(scope="module")
def full() -> dataset.Dataset:
    return dataset.load(builder.FULL_PATH)


@pytest.fixture(scope="module")
def sliced() -> dataset.Dataset:
    return dataset.load(builder.SLICE_PATH)


@needs_corpus
def test_every_manifest_selection_agrees_with_the_full_corpus(full, sliced):
    """The contract itself: same deliveries, same order, every selection."""
    assert builder.disagreements(full, sliced) == []


@needs_corpus
def test_the_committed_slice_is_what_the_builder_would_write(full, sliced):
    """Otherwise the builder documents a file it no longer produces."""
    assert ([d["product_id"] for d in sliced.deliveries]
            == [d["product_id"] for d in builder.select(full)])


@needs_corpus
def test_busiest_depot_survives_the_slicing(full, sliced):
    """`cluster_with_outliers` measures from whichever depot this returns.

    It reads the first `_DEPOT_SAMPLE` deliveries in *file order*, so a slice
    that reorders -- or that drops one of that prefix -- silently re-centres
    every outlier round in the examples.
    """
    assert full.busiest_depot()["name"] == sliced.busiest_depot()["name"]


def test_the_slice_is_shipped_and_loadable(sliced):
    """What CI can check without the corpus: the artifact is really there."""
    assert len(sliced.deliveries) > 1_000
    assert len(sliced.depots) == 6
    assert sliced.meta["slice_of"] == "deliveries_cr.json"
