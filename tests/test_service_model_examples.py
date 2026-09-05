"""The shipped delivery models rebuild the examples they describe.

A model file is only worth anything if it produces the problem somebody would
otherwise have written by hand, so this checks it against the hand-written one.
`models/signed-envelopes.json` is `fleet/tw/envelope_round.py`;
`models/mixed-parcels.json` is `fleet/rich/heterogeneous_fleet.py`.

The models are deliberately unalike -- one is time-bound with disjoint windows
and grams, the other capacity-bound with three vehicle classes and kilograms --
because a schema that fits only one of them fits nothing.

Bridges the examples and the platform, the way `test_sample_slice.py` does, and
lives here rather than under `tests/vrp/` for that reason: nothing in `vrp`
should import an example.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "examples" / "src"))
sys.path.insert(0, str(REPO / "examples" / "src" / "fleet" / "tw"))
sys.path.insert(0, str(REPO / "examples" / "src" / "fleet" / "rich"))

import dataset
import envelope_round
import heterogeneous_fleet

from vrp import servicemodel
from vrp.model import TravelMatrix


def flat_matrix(size: int) -> TravelMatrix:
    """Any matrix will do: both sides are handed the same one."""
    rows = tuple(tuple(0 if i == j else 600 for j in range(size))
                 for i in range(size))
    return TravelMatrix(version="flat", durations=rows, distances=rows)


def as_records(deliveries: list[dict]) -> list[dict]:
    """The corpus, normalised to what a model expects: `id`, `lat`, `lon`, rest.

    The model names the fields it reads, so `vrp` never learns a corpus's
    spelling. This is the adapter an integrator would write once.
    """
    return [{"id": d["product_id"], "lat": d["latitude"], "lon": d["longitude"],
             "weight_kg": d["weight_kg"],
             "service_minutes": d["service_minutes"]} for d in deliveries]


def test_signed_envelopes_rebuilds_its_example():
    deliveries, depot = dataset.load(dataset.DEFAULT_PATH).nearest(20)
    matrix = flat_matrix(len(deliveries) + 1)

    by_hand = envelope_round.build(depot, deliveries, matrix, 3).to_dict()
    from_model = servicemodel.build(
        servicemodel.model_for("signed-envelopes"),
        [{"id": "DEPOT", "lat": depot["latitude"], "lon": depot["longitude"]}],
        as_records(deliveries), matrix).to_dict()

    for part in ("locations", "orders", "vehicles"):
        assert by_hand[part] == from_model[part], part


def test_mixed_parcels_rebuilds_its_example():
    deliveries, depots = dataset.load(dataset.DEFAULT_PATH).around_each_depot(12)
    matrix = flat_matrix(len(deliveries) + len(depots))

    by_hand = heterogeneous_fleet.build(depots, deliveries, matrix,
                                        open_routes=False).to_dict()
    from_model = servicemodel.build(
        servicemodel.model_for("mixed-parcels"),
        [{"id": d["name"], "lat": d["latitude"], "lon": d["longitude"]}
         for d in depots],
        as_records(deliveries), matrix).to_dict()

    assert by_hand["id"] == from_model["id"]
    assert by_hand["locations"] == from_model["locations"]
    assert by_hand["orders"] == from_model["orders"]
    # Vehicle *ids* differ and only ids: the example shortens its depot name
    # with `.split()[0]`, so its motorbike is `MOTO@Guadalupe` where the model
    # names the depot it actually starts at, `MOTO@Guadalupe (San Jose)`.
    # Contorting the schema to reproduce an example's cosmetic spelling would be
    # the wrong way round, so the difference is asserted rather than hidden.
    assert ([dict(v, id=None) for v in by_hand["vehicles"]]
            == [dict(v, id=None) for v in from_model["vehicles"]])
    assert [v["id"] for v in by_hand["vehicles"]] != [
        v["id"] for v in from_model["vehicles"]]


def test_every_shipped_model_builds_something():
    """A model that ships and cannot build is worse than no model."""
    deliveries, depot = dataset.load(dataset.DEFAULT_PATH).nearest(8)
    records = as_records(deliveries)
    depots = [{"id": "DEPOT", "lat": depot["latitude"], "lon": depot["longitude"]}]
    for path in sorted(servicemodel.MODELS.glob("*.json")):
        if path.name == "categories.json":
            continue
        model = servicemodel.model_for(path.stem)
        problem = servicemodel.build(model, depots, records,
                                     flat_matrix(len(records) + 1))
        assert problem.orders and problem.vehicles, path.stem


def test_the_category_map_resolves_in_both_directions():
    """`gateway/src/config.rs`'s contract, applied to models.

    Every mapped category resolves to a model that ships, and every shipped
    model is reachable from the map. A model nothing maps to is dead weight
    nobody will notice; a category mapping to nothing is a 500 in waiting.
    """
    mapping = json.loads((servicemodel.MODELS / "categories.json").read_text())
    shipped = {p.stem for p in servicemodel.MODELS.glob("*.json")
               if p.name != "categories.json"}

    assert set(mapping.values()) <= shipped, (
        f"categories map to models that do not ship: "
        f"{sorted(set(mapping.values()) - shipped)}")
    assert shipped <= set(mapping.values()), (
        f"models nothing maps to: {sorted(shipped - set(mapping.values()))}")

    for category, name in mapping.items():
        assert category in servicemodel.model_for(name)["applies_to"], (
            f"{name} is mapped from {category!r} but does not claim it")
