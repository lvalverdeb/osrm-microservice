"""The gate a corpus derived from real deliveries passes to be written — `T-88`.

`NFR-07` requires benchmark corpora to be anonymised and coordinate-obfuscated
before leaving the production boundary. Everything in `vrp/bench` today is
*specified* rather than collected, so no address has ever left -- but that is a
property of where the fixtures came from, not a control. The day a corpus is
derived from real deliveries there is nothing to stop it being written, and the
way that failure surfaces is that it is already public.

**What is actually guaranteed.** Stripping the identifiers is the strong half:
an `order_id` joins straight back to the production system, and without it a
record is a weight at a place. Moving the coordinate is the weak half. It
defeats the doorstep; it does not defeat a determined match against a known
address list, and no grid resolution rescues the isolated rural delivery that
is the only record in its cell. Measured on the shipped corpus: at a 500 m grid
73% of stops collapse onto a shared point and 14% are still alone in their
cell. Coarsening buys little anonymity and costs the benchmark its geometry.

**Why 50 m.** Derived from the corpus rather than rounded up for comfort: the
median nearest-neighbour distance over all 50,000 shipped stops is 66 m, and a
50 m grid displaces a point by at most 35 m -- so a stop never moves past its
nearest neighbour, and the local ordering a solver exploits survives the
obfuscation. `MEDIAN_SPACING_METRES` pins that measurement at 60 rather than 66
so a regenerated corpus drifting a few metres denser does not fail the suite
for no reason; the tests check the pin against whatever corpus is present, so
it cannot quietly go stale.

**Why a content check rather than a type.** `write_corpus` takes `Anonymised`,
but it re-reads the records before writing. A gate that trusts a type trusts
whoever constructed it, and the value here is refusing the raw record, not
refusing the caller who forgot the ceremony.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

GRID_METRES = 50

# Measured over all 50,000 stops of the shipped corpus (median 66 m), pinned a
# little under it for headroom. The grid must displace a stop by less than this
# or obfuscation starts reordering near neighbours, which is the benchmark's
# geometry rather than a privacy property.
MEDIAN_SPACING_METRES = 60

# Every field the corpus carries, and what happens to it. `servicemodel`'s
# `COVERS`/`EXCLUDES` idiom: a field nobody has classified is refused rather
# than passed through, because the unclassified field is the one that turns out
# to carry the customer's name.
IDENTIFYING = frozenset({
    "order_id",    # joins back to the production system; the re-identification key
    "product_id",  # encodes a date and a sequence, so it joins back too
})
COARSENED = frozenset({"latitude", "longitude"})
SAFE = frozenset({
    "province",          # a region, not an address
    "hub",               # an operational facility; neither customer nor driver
    "gam",               # inside the metropolitan area or not
    "category",          # what is being carried, which is what a benchmark measures
    "weight_kg",         # ditto
    "units",             # ditto
    "priority",          # ditto
    "service_minutes",   # ditto
})
CLASSIFIED = IDENTIFYING | COARSENED | SAFE


@dataclass(frozen=True)
class Anonymised:
    """Records that have been through `anonymise`, and how far they moved."""

    records: tuple[dict[str, Any], ...]
    grid_metres: int
    salt: str


def metres_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Ground distance between two (lat, lon) pairs, flat-earth at this scale."""
    (lat_a, lon_a), (lat_b, lon_b) = a, b
    mid = math.radians((lat_a + lat_b) / 2)
    return math.hypot((lon_b - lon_a) * 111_320 * math.cos(mid),
                      (lat_b - lat_a) * 110_540)


def unclassified(records: list[dict[str, Any]]) -> list[str]:
    """Field names no rule above accounts for, sorted."""
    seen: set[str] = set()
    for record in records:
        seen |= set(record)
    return sorted(seen - CLASSIFIED)


def identifying(records: list[dict[str, Any]]) -> list[str]:
    """Identity-bearing field names still present, sorted."""
    seen: set[str] = set()
    for record in records:
        seen |= set(record) & IDENTIFYING
    return sorted(seen)


def _opaque(record: dict[str, Any], salt: str) -> str:
    """A stable key that is not a lookup key.

    Salted, so the same corpus re-derived is byte-identical and the same record
    in two corpora under two salts cannot be matched up.
    """
    joined = "|".join(str(record.get(field, "")) for field in sorted(IDENTIFYING))
    return hashlib.blake2s(f"{salt}|{joined}".encode(), digest_size=8).hexdigest()


def _on_grid(value: float, metres_per_degree: float, grid_metres: int) -> float:
    cell = grid_metres / metres_per_degree
    return round(round(value / cell) * cell, 6)


def anonymise(records: list[dict[str, Any]], *, salt: str,
              grid_metres: int = GRID_METRES) -> Anonymised:
    """Strip the identifiers and move the coordinates off the doorstep.

    Args:
        records: delivery records as the corpus carries them.
        salt: makes the opaque ids unmatchable across corpora. Keep it with the
            corpus if the derivation must be reproducible, and away from the
            corpus if it must not be reversed.
        grid_metres: obfuscation grid. The default is derived from stop spacing
            -- see the module docstring before widening it.

    Returns:
        `Anonymised`, which is what `write_corpus` accepts.

    Raises:
        ValueError: if any record carries a field nobody has classified. The
            refusal names the fields: a new column in the corpus is a decision
            somebody has to make, not a default to inherit.
    """
    strays = unclassified(records)
    if strays:
        raise ValueError(
            f"refusing to anonymise records carrying unclassified field(s): "
            f"{', '.join(strays)}; add each to IDENTIFYING, COARSENED or SAFE "
            "in vrp/anonymise.py with a reason, because a field nobody has "
            "decided about is the one that carries an address")

    clean = []
    for record in records:
        kept = {k: v for k, v in record.items() if k in SAFE}
        kept["id"] = _opaque(record, salt)
        lat = record["latitude"]
        kept["latitude"] = _on_grid(lat, 110_540, grid_metres)
        kept["longitude"] = _on_grid(record["longitude"],
                                     111_320 * math.cos(math.radians(lat)),
                                     grid_metres)
        clean.append(kept)
    return Anonymised(records=tuple(clean), grid_metres=grid_metres, salt=salt)


def write_corpus(path: Path, payload: Anonymised | list[dict[str, Any]]) -> None:
    """Write a derived corpus, or refuse and say which field stopped it.

    Args:
        path: where the corpus goes.
        payload: the result of `anonymise`. Anything else is the failure this
            gate exists for.

    Raises:
        ValueError: if the records still carry identity, naming the fields.
        TypeError: if they never went through `anonymise` -- stripping the
            identifiers by hand leaves the coordinates where they were
            collected, which is half the requirement.

        Nothing is written either way; a partly written corpus is a leaked one.
    """
    records = list(payload.records) if isinstance(payload, Anonymised) else payload
    leaking = identifying(records)
    if leaking:
        raise ValueError(
            f"refusing to write {path.name}: records still carry "
            f"{', '.join(leaking)}. Pass them through vrp.anonymise.anonymise "
            "first -- NFR-07 puts this on the write because that is the step "
            "that leaves the production boundary")
    if not isinstance(payload, Anonymised):
        raise TypeError(
            f"refusing to write {path.name}: records did not come from "
            "vrp.anonymise.anonymise, so their coordinates are wherever they "
            "were collected")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "meta": {"anonymised": True, "grid_metres": payload.grid_metres,
                 "note": "Coordinates are grid-snapped; ids are salted digests. "
                         "See vrp/anonymise.py for what this does and does not "
                         "guarantee."},
        "deliveries": records,
    }, indent=2, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    """`python -m vrp.anonymise <in.json> <out.json> --salt S`.

    The supported path, so that exporting a corpus is a command rather than a
    script somebody writes at the moment they are in a hurry.
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="corpus as collected")
    parser.add_argument("destination", type=Path, help="where the safe one goes")
    parser.add_argument("--salt", required=True,
                        help="keep it away from the corpus it anonymised")
    parser.add_argument("--grid-metres", type=int, default=GRID_METRES)
    args = parser.parse_args(argv)

    payload = json.loads(args.source.read_text())
    records = payload["deliveries"] if isinstance(payload, dict) else payload
    write_corpus(args.destination,
                 anonymise(records, salt=args.salt,
                           grid_metres=args.grid_metres))
    print(f"{len(records)} records anonymised to {args.destination} "
          f"({args.grid_metres} m grid)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
