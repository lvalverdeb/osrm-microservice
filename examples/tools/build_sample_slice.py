"""Rebuild the committed corpus slice, and say what it guarantees.

`examples/data/deliveries_sample.json` is the corpus the examples fall back to
when the full `data/deliveries_cr.json` is not there -- on a fresh clone, and in
CI. The full corpus is 12 MB and reproducible only by snapping 50,000 points
through a live OSRM, so it is not committed; without a stand-in, every
dataset-backed example skipped and the gate proved nothing about them.

The slice is not a sample. A random tenth of the corpus would answer every
selection differently and quietly change what each example prints. It is the
*union of the slices the examples actually take*, so that every selection in
`MANIFEST` returns the same deliveries, in the same order, as the full corpus.

That is the contract, and `tests/test_sample_slice.py` checks it whenever the
full corpus is present.

What it does not cover: selections outside `MANIFEST`. `spread(n, pool=P)` for
`P` above `SPREAD_POOL` reaches past the ranked prefix the slice keeps, and any
`by_province` restriction re-ranks inside a subset the slice only partly holds.
Both are real limits rather than bugs -- the examples' own defaults stay inside
the contract, and an example that needs more should be added to `MANIFEST` and
the slice rebuilt.

Usage:
    uv run python examples/tools/build_sample_slice.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "examples" / "src"))

import dataset

FULL_PATH = REPO / "data" / "deliveries_cr.json"
SLICE_PATH = REPO / "examples" / "data" / "deliveries_sample.json"

# `spread` ranks the nearest `pool` and samples every n-th, so the whole pool
# has to be present or a spread round changes. This is the largest pool the
# slice underwrites; `dataset.Dataset.spread` defaults to it.
SPREAD_POOL = 2_000

# Every selection the examples make, observed rather than guessed: the gate was
# run with each `Dataset` method traced, so this is what the examples do when
# invoked the way `tests/test_examples_run.py` invokes them -- with defaults.
# An example that only reaches a selection behind a flag the gate never passes
# is not represented here, and the slice does not underwrite it.
#
# Entries may subsume one another -- `nearest(2_000)` asks for exactly the
# prefix `spread` already pins -- and that is left alone. The list is a record
# of what the examples call, not a minimal cover, so an entry going quiet when
# `SPREAD_POOL` changes is a property worth keeping.
MANIFEST: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...] = (
    ("around_each_depot", (24,), {}),
    ("cluster_with_outliers", (20, 2), {}),
    ("furthest", (3,), {}),
    ("furthest", (8,), {}),
    ("nearest", (12,), {}),
    ("nearest", (14,), {}),
    ("nearest", (18,), {}),
    ("nearest", (20,), {}),
    ("nearest", (40,), {}),
    # `verify_delivery_plan` works from `busiest_depot`, not `depots[0]`.
    ("nearest", (40,), {"depot_name": "*busiest*"}),
    ("nearest", (60,), {}),
    ("nearest", (120,), {}),
    ("nearest", (150,), {}),
    ("nearest", (300,), {}),
    ("nearest", (400,), {}),
    ("nearest", (600,), {}),
    ("nearest", (2_000,), {}),
    ("spread", (1,), {"depot_name": "Guadalupe (San Jose)"}),
    ("spread", (2,), {"depot_name": "Guadalupe (San Jose)"}),
    ("spread", (4,), {}),
    ("spread", (6,), {}),
    ("spread", (8,), {}),
    ("spread", (10,), {}),
    ("spread", (12,), {}),
    ("spread", (14,), {}),
    ("spread", (15,), {}),
    ("spread", (100,), {}),
)


def _ranked_by_degrees(corpus: dataset.Dataset,
                       depot: dict[str, Any]) -> list[dict[str, Any]]:
    """The corpus ordered as `nearest`, `spread` and `around_each_depot` order it."""
    return sorted(corpus.deliveries,
                  key=lambda d: dataset._square_degrees(d, depot))


def _ranked_by_metres(corpus: dataset.Dataset,
                      depot: dict[str, Any]) -> list[dict[str, Any]]:
    """The corpus ordered as `furthest` and `cluster_with_outliers` order it."""
    origin = (depot["latitude"], depot["longitude"])
    return sorted(corpus.deliveries,
                  key=lambda d: dataset.great_circle_metres(
                      origin, (d["latitude"], d["longitude"])))


def _depot(corpus: dataset.Dataset, name: str | None) -> dict[str, Any]:
    if name is None:
        return corpus.depots[0]
    if name == "*busiest*":
        return corpus.busiest_depot()
    return next(d for d in corpus.depots if d["name"] == name)


def needed_for(corpus: dataset.Dataset, strategy: str, args: tuple,
               kw: dict[str, Any]) -> list[dict[str, Any]]:
    """The deliveries that must be kept for one selection to agree.

    Rank alignment is the whole difficulty. A selection takes ranked positions,
    and the slice's ranking is the corpus's ranking restricted to what it keeps,
    so position *k* only survives if every delivery ranked above it survives
    too. Keeping the selection's own results is not enough; the prefix is.

    Args:
        corpus: The full corpus.
        strategy: A `Dataset` method name.
        args: Positional arguments the example passes.
        kw: Keyword arguments, plus `depot_name` naming a depot by name or
            `"*busiest*"` for whatever `busiest_depot` returns.

    Returns:
        Deliveries to keep, in no particular order.

    Raises:
        ValueError: If the strategy is not one the builder knows how to
            underwrite, which means the slice cannot promise it.
    """
    depot = _depot(corpus, kw.get("depot_name"))
    count = args[0] if args else 0
    if strategy == "nearest":
        return _ranked_by_degrees(corpus, depot)[:count]
    if strategy == "spread":
        pool = kw.get("pool", SPREAD_POOL)
        return _ranked_by_degrees(corpus, depot)[:min(pool, len(corpus.deliveries))]
    if strategy == "furthest":
        return _ranked_by_metres(corpus, depot)[-count:]
    if strategy == "cluster_with_outliers":
        ranked = _ranked_by_metres(corpus, _depot(corpus, kw.get("depot_name")
                                                  or "*busiest*"))
        outliers = args[1]
        return ranked[:max(count - outliers, 1)] + (ranked[-outliers:]
                                                    if outliers else [])
    if strategy == "around_each_depot":
        keep: list[dict[str, Any]] = []
        for each in corpus.depots:
            keep += _ranked_by_degrees(corpus, each)[:count]
        return keep
    raise ValueError(f"the slice cannot underwrite {strategy!r}")


def select(corpus: dataset.Dataset) -> list[dict[str, Any]]:
    """Every delivery the manifest requires, in the corpus's own file order.

    File order matters twice over: `busiest_depot` reads the first
    `dataset._DEPOT_SAMPLE` deliveries as a prefix, and
    `cluster_with_outliers` measures from whichever depot that returns. A slice
    that reorders picks a different depot and every outlier round changes.

    Args:
        corpus: The full corpus.

    Returns:
        The deliveries to commit.
    """
    keep = {d["product_id"] for d in corpus.deliveries[:dataset._DEPOT_SAMPLE]}
    for strategy, args, kw in MANIFEST:
        keep |= {d["product_id"] for d in needed_for(corpus, strategy, args, kw)}
    return [d for d in corpus.deliveries if d["product_id"] in keep]


def disagreements(full: dataset.Dataset,
                  sliced: dataset.Dataset) -> list[str]:
    """Manifest selections the slice answers differently from the full corpus.

    Args:
        full: The full corpus.
        sliced: The committed slice.

    Returns:
        One line per selection that disagrees. Empty when the contract holds.
    """
    out = []
    if full.busiest_depot()["name"] != sliced.busiest_depot()["name"]:
        out.append(f"busiest_depot: {full.busiest_depot()['name']!r} "
                   f"vs {sliced.busiest_depot()['name']!r}")

    def chosen(corpus: dataset.Dataset, strategy: str, args: tuple,
               kw: dict[str, Any]) -> list[str]:
        # The depot is resolved against whichever corpus is being asked, so
        # `*busiest*` means what the example would mean having loaded it.
        call = {k: v for k, v in kw.items() if k != "depot_name"}
        if "depot_name" in kw:
            call["depot"] = _depot(corpus, kw["depot_name"])
        return [d["product_id"]
                for d in getattr(corpus, strategy)(*args, **call)[0]]

    for strategy, args, kw in MANIFEST:
        if chosen(full, strategy, args, kw) != chosen(sliced, strategy,
                                                      args, kw):
            out.append(f"{strategy}{args} {kw}")
    return out


def main() -> int:
    """Rebuild the slice and refuse to write one that breaks the contract."""
    if not FULL_PATH.exists():
        raise SystemExit(
            f"no corpus at {FULL_PATH}. Build it first:\n"
            "    uv run --package osrm-api-gateway-examples \\\n"
            "        examples/src/vrp/generate_delivery_dataset.py")
    full = dataset.load(FULL_PATH)
    keep = select(full)
    payload = {"depots": full.depots, "deliveries": keep,
               "meta": {**full.meta, "count": len(keep),
                        "slice_of": FULL_PATH.name,
                        "note": "Committed slice. Reproduces the full corpus "
                                "for every selection in MANIFEST; see "
                                "examples/tools/build_sample_slice.py."}}
    SLICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Written through a temporary file and renamed: an example or a test run
    # concurrently should see the old slice or the new one, never half of one.
    scratch = SLICE_PATH.with_suffix(".json.tmp")
    scratch.write_text(json.dumps(payload, separators=(",", ":")))
    scratch.replace(SLICE_PATH)
    bad = disagreements(full, dataset.load(SLICE_PATH))
    for line in bad:
        print(f"  DISAGREES  {line}")
    print(f"{len(keep)} deliveries, {SLICE_PATH.stat().st_size / 1024:.0f} KB, "
          f"{len(MANIFEST)} selections underwritten")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
