"""Composition: fragments, and why they do not have precedence.

Six cities running one operation is six near-identical model files, which is
the repetition delivery models exist to remove. A master assembles *sections*
from named fragments instead, and a deployment variant adjusts the fields that
master declares `tunable` -- a contract, so a city may raise its courier count
and may not quietly change the capacity its feasibility depends on.

The rule that makes it debuggable is that composition is **disjoint**. Each
section is claimed by exactly one source, and two sources claiming the same one
is a conflict the loader refuses rather than something it resolves by order.
That removes the diamond problem outright: there is no "last wins" to reason
about, because overlap is illegal rather than ordered. Every resolved section
therefore has exactly one origin, and `provenance` can name it.

Fragments do not use fragments. One level, so "where did this value come from"
is answerable by looking at two files.
"""

from __future__ import annotations

import json

import pytest

from vrp import servicemodel


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A models directory with fragments, replacing the shipped one."""
    (tmp_path / "fragments").mkdir()
    monkeypatch.setattr(servicemodel, "MODELS", tmp_path)

    def write(where: str, name: str, body: dict) -> None:
        path = tmp_path / where / f"{name}.json" if where else tmp_path / f"{name}.json"
        path.write_text(json.dumps(body))

    write("fragments", "cr-business-hours", {
        "windows": [{"start": 28800, "end": 43200},
                    {"start": 46800, "end": 61200}]})
    write("fragments", "cr-daytime-shift", {
        "shift": {"start": 28800, "end": 57600}})
    write("fragments", "also-windows", {
        "windows": [{"start": 0, "end": 86400}]})
    # Contributes nothing but a `use`. Anything else would collide with the
    # model's own sections and raise for that reason instead, which is how the
    # first version of this fixture passed whether the depth guard existed or
    # not.
    write("fragments", "nested", {"use": ["cr-daytime-shift"]})
    return write


BASE = {
    "name": "couriers",
    "applies_to": ["Documents"],
    "problem_id": "envelopes",
    "quantity": {"dimension": "grams", "fixed": 200},
    "service": {"fixed_seconds": 600},
    "route": {"closed": True},
    "assignment": {"depot": "nearest"},
    "fleet": [{"class": "COURIER", "id": "COURIER-{n}", "per_depot": 2,
               "capacities": {"grams": 25000}}],
    "run": {"engine": "pyvrp", "budget": 800, "seed": 0,
            "objective": {"mode": "MIN_COST", "vehicle_fixed_cost": 50000,
                          "cost_per_metre": 1, "cost_per_second": 0}},
}


def test_a_fragment_contributes_its_sections(library):
    library("", "couriers", dict(BASE, use=["cr-business-hours",
                                            "cr-daytime-shift"]))
    resolved = servicemodel.resolve("couriers")
    assert resolved.model["shift"] == {"start": 28800, "end": 57600}
    assert len(resolved.model["windows"]) == 2
    # The model's own sections survive composition.
    assert resolved.model["quantity"] == {"dimension": "grams", "fixed": 200}


def test_two_fragments_claiming_one_section_is_a_conflict(library):
    """No precedence to reason about, so no diamond to resolve."""
    library("", "couriers", dict(BASE, use=["cr-business-hours",
                                            "also-windows"]))
    with pytest.raises(ValueError) as refused:
        servicemodel.resolve("couriers")
    message = str(refused.value)
    assert "windows" in message
    assert "cr-business-hours" in message and "also-windows" in message


def test_a_model_may_not_take_a_section_a_fragment_already_gave(library):
    """The model is a source like any other, not an override of last resort."""
    library("", "couriers", dict(BASE, use=["cr-daytime-shift"],
                                 shift={"start": 0, "end": 3600}))
    with pytest.raises(ValueError, match="shift"):
        servicemodel.resolve("couriers")


def test_fragments_do_not_use_fragments(library):
    """One level, so a value comes from this file or one named in it."""
    library("", "couriers", dict(BASE, use=["nested"]))
    with pytest.raises(ValueError, match="nested"):
        servicemodel.resolve("couriers")


def test_an_unknown_fragment_names_what_ships(library):
    library("", "couriers", dict(BASE, use=["cr-siesta"]))
    with pytest.raises(ValueError) as refused:
        servicemodel.resolve("couriers")
    assert "cr-siesta" in str(refused.value)
    assert "cr-business-hours" in str(refused.value)


def test_every_resolved_section_names_where_it_came_from(library):
    """Provenance is what makes a composed model debuggable rather than tidy."""
    library("", "couriers", dict(BASE, use=["cr-business-hours"]))
    resolved = servicemodel.resolve("couriers")
    assert resolved.provenance["windows"] == "fragment:cr-business-hours"
    assert resolved.provenance["quantity"] == "model:couriers"
    # A superset once overlays land: every section is covered, and an overlay
    # adds a dotted entry for each field it changed.
    assert set(resolved.provenance) >= set(resolved.model)


def test_a_model_using_nothing_resolves_to_itself(library):
    library("", "couriers", dict(BASE, windows=[{"start": 0, "end": 86400}],
                                 shift={"start": 0, "end": 86400}))
    resolved = servicemodel.resolve("couriers")
    assert resolved.model["windows"] == [{"start": 0, "end": 86400}]
    assert set(resolved.provenance.values()) == {"model:couriers"}


def test_a_resolved_model_still_builds(library):
    """Composition feeds the builder; it does not replace it."""
    library("", "couriers", dict(BASE, use=["cr-business-hours",
                                            "cr-daytime-shift"]))
    resolved = servicemodel.resolve("couriers")
    assert servicemodel.validate_keys(resolved.model) == []
    assert servicemodel.run_config(resolved.model).engine == "pyvrp"


# --------------------------------------------------------------------------
# Deployment overlays: one level, over a declared surface
# --------------------------------------------------------------------------
# Fragments remove duplication between models. The overlay is what lets six
# cities differ from one master without becoming six models.
#
# Three rules keep it debuggable. Exactly one level, so a value comes from the
# master or the overlay and there are two files to look in rather than a chain.
# Only fields the master declares `tunable`, which makes the master a contract:
# a city may raise its courier count and may not quietly change the capacity its
# feasibility depends on. And only fields that already exist, so a typo is a
# failed override rather than a new setting nobody reads.

MASTER = dict(BASE, name="couriers", tunable=["shift.end",
                                              "fleet.COURIER.per_depot"])


def test_a_variant_changes_what_the_master_allows(library):
    library("", "couriers", dict(MASTER, shift={"start": 28800, "end": 57600},
                                 windows=[{"start": 28800, "end": 57600}]))
    library("", "couriers-cartago", {"name": "couriers-cartago",
                                     "base": "couriers",
                                     "set": {"fleet.COURIER.per_depot": 5}})
    resolved = servicemodel.resolve("couriers-cartago")
    assert resolved.model["fleet"][0]["per_depot"] == 5
    # Everything else is the master's, untouched.
    assert resolved.model["quantity"] == {"dimension": "grams", "fixed": 200}
    assert resolved.model["fleet"][0]["capacities"] == {"grams": 25000}


def test_a_field_outside_the_tunable_surface_is_refused(library):
    """The master is a contract, not a starting point."""
    library("", "couriers", dict(MASTER, shift={"start": 28800, "end": 57600},
                                 windows=[{"start": 28800, "end": 57600}]))
    library("", "greedy", {"name": "greedy", "base": "couriers",
                           "set": {"fleet.COURIER.capacities": {"grams": 1}}})
    with pytest.raises(ValueError) as refused:
        servicemodel.resolve("greedy")
    assert "capacities" in str(refused.value)
    assert "shift.end" in str(refused.value)


def test_an_overlay_cannot_introduce_a_field(library):
    """A typo must be a failed override, not a setting nobody reads."""
    library("", "couriers", dict(MASTER, shift={"start": 28800, "end": 57600},
                                 windows=[{"start": 28800, "end": 57600}],
                                 tunable=["shift.ends"]))
    library("", "typo", {"name": "typo", "base": "couriers",
                         "set": {"shift.ends": 61200}})
    with pytest.raises(ValueError, match="shift.ends"):
        servicemodel.resolve("typo")


def test_a_list_element_is_addressed_by_identity_not_position(library):
    """`fleet.COURIER.per_depot`, never `fleet[0].per_depot`.

    Positional addressing is a defect waiting for somebody to reorder a file:
    the overlay would keep applying and would start changing a different
    vehicle.
    """
    two = [dict(BASE["fleet"][0]),
           {"class": "VAN", "id": "VAN-{n}", "per_depot": 1,
            "capacities": {"grams": 900000}}]
    library("", "couriers", dict(MASTER, fleet=two,
                                 shift={"start": 28800, "end": 57600},
                                 windows=[{"start": 28800, "end": 57600}],
                                 tunable=["fleet.VAN.per_depot"]))
    library("", "vanheavy", {"name": "vanheavy", "base": "couriers",
                             "set": {"fleet.VAN.per_depot": 7}})
    fleet = servicemodel.resolve("vanheavy").model["fleet"]
    assert [f["class"] for f in fleet] == ["COURIER", "VAN"]
    assert fleet[0]["per_depot"] == 2, "the courier must be untouched"
    assert fleet[1]["per_depot"] == 7


def test_a_variant_of_a_variant_is_refused(library):
    """Depth one is what makes `T-96`'s failure attribution bounded."""
    library("", "couriers", dict(MASTER, shift={"start": 28800, "end": 57600},
                                 windows=[{"start": 28800, "end": 57600}]))
    library("", "city", {"name": "city", "base": "couriers",
                         "tunable": ["shift.end"],
                         "set": {"shift.end": 61200}})
    library("", "district", {"name": "district", "base": "city",
                             "set": {"shift.end": 64800}})
    with pytest.raises(ValueError, match="city"):
        servicemodel.resolve("district")


def test_provenance_names_the_overlay_for_what_it_changed(library):
    library("", "couriers", dict(MASTER, shift={"start": 28800, "end": 57600},
                                 windows=[{"start": 28800, "end": 57600}]))
    library("", "late", {"name": "late", "base": "couriers",
                         "set": {"shift.end": 61200}})
    resolved = servicemodel.resolve("late")
    assert resolved.model["shift"]["end"] == 61200
    assert resolved.provenance["shift.end"] == "overlay:late"
    assert resolved.provenance["shift"] == "model:couriers"


def test_a_master_declaring_nothing_tunable_accepts_no_overlay(library):
    library("", "frozen", dict(BASE, name="frozen",
                               shift={"start": 28800, "end": 57600},
                               windows=[{"start": 28800, "end": 57600}]))
    library("", "wishful", {"name": "wishful", "base": "frozen",
                            "set": {"shift.end": 61200}})
    with pytest.raises(ValueError, match="tunable"):
        servicemodel.resolve("wishful")


# Categories map to masters. A variant is chosen by a deployment, not by the
# category of the thing being delivered, so "every shipped model is reachable"
# has to mean "reachable, directly or through its base" -- otherwise the first
# variant to ship fails an invariant about dead weight while being anything but.


def test_a_variant_is_reachable_through_the_master_its_category_maps_to(library):
    library("", "couriers", MASTER)
    library("", "heredia", {"name": "heredia", "base": "couriers",
                            "set": {"fleet.COURIER.per_depot": 5}})

    assert servicemodel.unreachable({"Documents": "couriers"}) == []


def test_a_variant_of_a_model_no_category_maps_to_is_dead_weight(library):
    library("", "couriers", MASTER)
    library("", "orphan", dict(MASTER, name="orphan"))
    library("", "heredia", {"name": "heredia", "base": "orphan",
                            "set": {"fleet.COURIER.per_depot": 5}})

    assert servicemodel.unreachable({"Documents": "couriers"}) == [
        "heredia", "orphan"]
