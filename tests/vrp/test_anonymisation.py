"""`T-88` — the gate anything derived from real deliveries passes to be written.

`NFR-07`: customer addresses and driver identities are PII, and benchmark
corpora must be anonymised and coordinate-obfuscated before leaving the
production boundary. Today's corpora are generated, so no address has ever
left -- but that is a property of where the fixtures came from, not a control.
The day a corpus is derived from real deliveries, nothing stops it being
written, and nobody finds out until it is public.

What this is honest about. Stripping the identifiers is the guarantee: an
`order_id` joins straight back to the production system, and once it is gone
the record is a weight at a place. Moving the coordinate is the weaker half --
it defeats the doorstep, not a determined match against a known address list,
and no grid fixes the isolated rural delivery that is alone in its cell at any
resolution. Measured on the shipped corpus: at 500 m, 73% of stops collapse
onto a shared point and 14% are *still* the only record in their cell. Coarser
buys little and costs the benchmark its geometry, which is why the grid is
derived from stop spacing rather than rounded up for comfort.
"""

from __future__ import annotations

import json
import math

import pytest

from vrp import anonymise as anon

RAW = [
    {"product_id": "CR-20260825-000001", "order_id": "ORD-000001",
     "province": "Alajuela", "hub": "Alajuela Central", "gam": True,
     "latitude": 10.007028, "longitude": -84.225307, "category": "Electronics",
     "weight_kg": 3.41, "units": 5, "priority": "express",
     "service_minutes": 12},
    {"product_id": "CR-20260825-000002", "order_id": "ORD-000002",
     "province": "San José", "hub": "San José Centro", "gam": True,
     "latitude": 9.932000, "longitude": -84.079000, "category": "Documents",
     "weight_kg": 0.2, "units": 1, "priority": "standard",
     "service_minutes": 10},
]


def test_the_unanonymised_path_refuses_and_names_the_field(tmp_path):
    """The refusal has to say *what* is wrong, or it teaches nobody."""
    with pytest.raises(ValueError) as refusal:
        anon.write_corpus(tmp_path / "corpus.json", RAW)

    assert "order_id" in str(refusal.value)
    assert "product_id" in str(refusal.value)
    assert not (tmp_path / "corpus.json").exists()


def test_the_gate_is_on_the_content_not_on_the_ceremony(tmp_path):
    """A hand-built wrapper round raw records is refused like any other.

    A gate that trusts a type is a gate that trusts whoever constructed it.
    """
    wrapped = anon.Anonymised(records=tuple(RAW), grid_metres=anon.GRID_METRES,
                              salt="s")
    with pytest.raises(ValueError) as refusal:
        anon.write_corpus(tmp_path / "corpus.json", wrapped)

    assert "order_id" in str(refusal.value)


def test_anonymising_strips_identity_and_keeps_what_a_benchmark_needs():
    clean = anon.anonymise(RAW, salt="pepper")

    for before, after in zip(RAW, clean.records, strict=True):
        assert "order_id" not in after
        assert "product_id" not in after
        assert after["weight_kg"] == before["weight_kg"]
        assert after["category"] == before["category"]
        assert after["service_minutes"] == before["service_minutes"]
        assert after["id"] not in (before["order_id"], before["product_id"])


def test_coordinates_are_moved_off_the_doorstep():
    """Six decimal places is ten centimetres. That is a front door."""
    clean = anon.anonymise(RAW, salt="pepper")

    for before, after in zip(RAW, clean.records, strict=True):
        moved = anon.metres_between(
            (before["latitude"], before["longitude"]),
            (after["latitude"], after["longitude"]))
        assert 0 < moved <= anon.GRID_METRES * math.sqrt(2) / 2


def corpus() -> tuple[list[dict], str]:
    """The densest delivery corpus on this machine, and which one it is.

    `data/deliveries_cr.json` is generated and gitignored, so CI only ever has
    the committed slice -- whose stops sit 89 m apart against the full corpus's
    66 m. A test that just measured whatever file was present would let CI
    validate the grid against a *thinner* corpus than production data, which is
    how a 100 m grid would pass review. Hence the pinned constant below.
    """
    full = anon.REPO / "data/deliveries_cr.json"
    slice_ = anon.REPO / "examples/data/deliveries_sample.json"
    path = full if full.exists() else slice_
    return json.loads(path.read_text())["deliveries"], path.name


def test_the_displacement_stays_under_the_spacing_it_has_to_preserve():
    """The grid is derived, not rounded: worst-case displacement below the
    median nearest-neighbour distance, so a stop never moves past its nearest
    neighbour and the local ordering a solver exploits survives."""
    assert anon.GRID_METRES * math.sqrt(2) / 2 < anon.MEDIAN_SPACING_METRES, (
        f"a {anon.GRID_METRES} m grid moves a stop up to "
        f"{anon.GRID_METRES * math.sqrt(2) / 2:.0f} m, past the "
        f"{anon.MEDIAN_SPACING_METRES} m spacing it is supposed to preserve")


def test_the_pinned_spacing_is_not_optimistic_about_the_real_corpus():
    """The constant above is only worth anything if it still matches the data.

    Buckets keep this quick and honest: a neighbour outside the 3x3 cells round
    a point is further than FAR, hence above the median, so it cannot move the
    median. The first draft of this measured spacing *within* a 400-record
    slice of an ordered corpus -- stops hundreds of metres apart -- and passed
    at a 500 m grid.
    """
    records, source = corpus()
    points = [(r["latitude"], r["longitude"]) for r in records]

    FAR = 500
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for lat, lon in points:
        buckets.setdefault((int(lat * 110_540 // FAR),
                            int(lon * 111_320 // FAR)), []).append((lat, lon))

    spacing = []
    for point in points[::10]:
        home = (int(point[0] * 110_540 // FAR), int(point[1] * 111_320 // FAR))
        near = [q for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for q in buckets.get((home[0] + dx, home[1] + dy), [])
                if q != point]
        spacing.append(min((anon.metres_between(point, q) for q in near),
                           default=float(FAR)))
    spacing.sort()
    median = spacing[len(spacing) // 2]

    assert median < FAR, "bucket fallback would be masking the real spacing"
    assert median >= anon.MEDIAN_SPACING_METRES, (
        f"{source} has stops {median:.0f} m apart, denser than the pinned "
        f"{anon.MEDIAN_SPACING_METRES} m; re-derive GRID_METRES rather than "
        "moving the pin down to meet it")


def test_the_same_salt_reproduces_the_corpus_and_another_does_not():
    """A benchmark must be reproducible; an id must not be a lookup key."""
    once = anon.anonymise(RAW, salt="pepper")
    again = anon.anonymise(RAW, salt="pepper")
    elsewhere = anon.anonymise(RAW, salt="other")

    assert once.records == again.records
    assert [r["id"] for r in once.records] != [r["id"] for r in elsewhere.records]


def test_a_field_nobody_has_classified_refuses_rather_than_passing_through():
    """`servicemodel`'s contract idiom. A field nobody decided about is the
    one that carries the customer's name."""
    with pytest.raises(ValueError) as refusal:
        anon.anonymise([dict(RAW[0], recipient_name="Ana Solís")], salt="s")

    assert "recipient_name" in str(refusal.value)


def test_every_field_the_generator_writes_has_been_classified():
    """So adding a field to the corpus fails the suite until someone decides."""
    records, source = corpus()
    written = {field for r in records for field in r}

    assert written <= anon.CLASSIFIED, (
        f"{source} carries unclassified field(s): "
        f"{sorted(written - anon.CLASSIFIED)}")


def test_a_written_corpus_round_trips(tmp_path):
    path = tmp_path / "corpus.json"
    anon.write_corpus(path, anon.anonymise(RAW, salt="pepper"))
    written = json.loads(path.read_text())

    assert written["meta"]["grid_metres"] == anon.GRID_METRES
    assert len(written["deliveries"]) == len(RAW)
    assert all("order_id" not in r for r in written["deliveries"])


def test_stripping_by_hand_is_not_enough(tmp_path):
    """Identifiers gone, coordinates still at the front door. The type is the
    only thing that can tell the difference, so here it is load-bearing."""
    by_hand = [{k: v for k, v in r.items() if k not in anon.IDENTIFYING}
               for r in RAW]

    with pytest.raises(TypeError, match="anonymise"):
        anon.write_corpus(tmp_path / "corpus.json", by_hand)
    assert not (tmp_path / "corpus.json").exists()
