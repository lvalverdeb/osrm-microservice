"""The delivery model's coverage contract.

A model is JSON that builds a `Problem`, so the question that decides whether
it is honest is: **which of the domain's fields can it set, and which has it
decided not to?** This is the first half of `T-94`, which is still open: the
builder is not written, and these tests say nothing about it.

`_vehicle_from_dict` reconstructed 9 of `Vehicle`'s 28 fields until `T-89`, and
nothing failed -- a snapshot round-tripped, and nineteen fields were dropped in
silence. The throwaway spike for this task managed 8 of 28 and would have
shipped the same defect. Both were written by hand, and a hand-written list is
how a loader silently lags the model it loads.

So the contract is machine-checked against the dataclasses themselves. Every
field is either covered, or excluded with a stated reason; a field that is
neither fails this file, and so does an entry naming a field that no longer
exists. Adding a field to `Vehicle` breaks these tests until somebody decides
about it, which is the entire point.

The pattern is `gateway/src/config.rs`, whose `settings!` macro exports
`DEFAULTS` and whose tests check both directions -- every key in `app.env`
resolves to a setting, every default matches the committed value. Its docstring
calls it "the cheapest correctness gate this port has".
"""

from __future__ import annotations

import dataclasses as dc

import pytest

from vrp import servicemodel
from vrp.model import Location, Order, StopSpec, TimeWindow, Vehicle

GOVERNED = (Vehicle, Order, StopSpec, Location, TimeWindow)


def field_names(cls: type) -> set[str]:
    return {f.name for f in dc.fields(cls)}


@pytest.mark.parametrize("cls", GOVERNED, ids=lambda c: c.__name__)
def test_every_field_is_covered_or_excluded(cls: type):
    """No field may be unaccounted for. This is the test T-89 needed."""
    covered = set(servicemodel.COVERS.get(cls, {}))
    excluded = set(servicemodel.EXCLUDES.get(cls, {}))
    unaccounted = field_names(cls) - covered - excluded
    assert not unaccounted, (
        f"{cls.__name__} has fields the model contract does not mention: "
        f"{sorted(unaccounted)}. Cover them, or exclude them with a reason.")


@pytest.mark.parametrize("cls", GOVERNED, ids=lambda c: c.__name__)
def test_the_contract_names_no_field_that_does_not_exist(cls: type):
    """A renamed field must break the contract rather than rot inside it."""
    named = set(servicemodel.COVERS.get(cls, {})) | set(servicemodel.EXCLUDES.get(cls, {}))
    stale = named - field_names(cls)
    assert not stale, f"{cls.__name__}: contract names fields that are gone: {sorted(stale)}"


@pytest.mark.parametrize("cls", GOVERNED, ids=lambda c: c.__name__)
def test_a_field_is_not_both_covered_and_excluded(cls: type):
    both = set(servicemodel.COVERS.get(cls, {})) & set(servicemodel.EXCLUDES.get(cls, {}))
    assert not both, f"{cls.__name__}: covered and excluded at once: {sorted(both)}"


@pytest.mark.parametrize("cls", GOVERNED, ids=lambda c: c.__name__)
def test_every_exclusion_states_why(cls: type):
    """An exclusion without a reason is an omission wearing a decision's clothes."""
    for name, reason in servicemodel.EXCLUDES.get(cls, {}).items():
        assert reason and len(reason) > 20, (
            f"{cls.__name__}.{name} is excluded with no usable reason: {reason!r}")


def test_the_schema_is_derived_from_the_contract():
    """The keys a file may use come from `COVERS`, so they cannot drift from it."""
    keys = servicemodel.known_keys()
    assert {"fleet", "shift", "quantity", "windows", "service", "route",
            "assignment", "declinable", "order"} <= keys
    # Nothing invented: every derived key is the head of a contract path.
    heads = {path.split(".")[0].removesuffix("[]")
             for mapping in servicemodel.COVERS.values()
             for path in mapping.values()}
    assert keys == heads


def test_an_unknown_key_is_refused_rather_than_ignored():
    """A typo that is ignored is a setting that silently did not apply."""
    complaints = servicemodel.validate_keys({"name": "x", "flet": []})
    assert len(complaints) == 1
    assert "'flet'" in complaints[0]


def test_a_clean_file_draws_no_complaint():
    assert servicemodel.validate_keys(
        {"name": "signed-envelopes", "applies_to": ["Documents"],
         "quantity": {}, "service": {}, "windows": [], "shift": {},
         "route": {}, "assignment": {}, "fleet": []}) == []


def test_the_contract_governs_every_type_a_model_builds():
    """A new domain type must be decided about, not silently ungoverned."""
    mentioned = set(servicemodel.COVERS) | set(servicemodel.EXCLUDES)
    assert mentioned == set(GOVERNED), (
        "the contract and this test disagree about which types a model builds: "
        f"{sorted(c.__name__ for c in mentioned ^ set(GOVERNED))}")
