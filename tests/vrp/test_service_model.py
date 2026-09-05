"""The delivery model's coverage contract.

A model is JSON that builds a `Problem`, so the question that decides whether
it is honest is: **which of the domain's fields can it set, and which has it
decided not to?** `T-94`'s contract, tested here; the builder it feeds is
checked against the examples it rebuilds in `tests/test_service_model_examples.py`.

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


# --------------------------------------------------------------------------
# Building a Problem
# --------------------------------------------------------------------------
# The builder takes records already normalised to `id`, `lat`, `lon` plus
# whatever else the model's `from_field` keys name. `vrp` never learns a
# corpus's spelling: the model file is the adapter between a data source and
# the domain, which is why `Order.id` and `Location.lat` are excluded from the
# contract -- the model does not choose those values, it only says where they
# come from.

DEPOTS = [{"id": "DEPOT", "lat": 9.9472, "lon": -84.0531}]
STOPS = [
    {"id": "A", "lat": 9.9333, "lon": -84.0785, "weight_kg": 4.2, "minutes": 6},
    {"id": "B", "lat": 9.9981, "lon": -84.1165, "weight_kg": 11.7, "minutes": 9},
]

ENVELOPE_MODEL = {
    "name": "test-envelopes",
    "applies_to": ["Documents"],
    "problem_id": "envelopes",
    "quantity": {"dimension": "grams", "fixed": 200},
    "service": {"fixed_seconds": 600},
    "windows": [{"start": 28800, "end": 43200},
                {"start": 46800, "end": 61200}],
    "shift": {"start": 28800, "end": 57600},
    "route": {"closed": True},
    "assignment": {"depot": "nearest"},
    "fleet": [{"class": "COURIER", "id": "COURIER-{n}", "per_depot": 2,
               "capacities": {"grams": 25000}}],
}


def matrix_for(count: int):
    from vrp.model import TravelMatrix
    rows = tuple(tuple(0 if i == j else 600 for j in range(count))
                 for i in range(count))
    return TravelMatrix(version="m", durations=rows, distances=rows)


def test_a_model_builds_the_problem_it_describes():
    problem = servicemodel.build(ENVELOPE_MODEL, DEPOTS, STOPS, matrix_for(3))
    assert problem.id == "envelopes"
    assert [loc.id for loc in problem.locations] == ["DEPOT", "A", "B"]
    assert [o.id for o in problem.orders] == ["A", "B"]
    assert [v.id for v in problem.vehicles] == ["COURIER-1", "COURIER-2"]


def test_a_fixed_quantity_is_the_same_for_every_order():
    problem = servicemodel.build(ENVELOPE_MODEL, DEPOTS, STOPS, matrix_for(3))
    assert [o.quantities for o in problem.orders] == [{"grams": 200}] * 2


def test_a_quantity_read_from_a_field_rounds_up():
    """Understating a load is the unsafe direction; see `dataset.load_kg`."""
    model = dict(ENVELOPE_MODEL,
                 quantity={"dimension": "kg", "from_field": "weight_kg",
                           "round": "up"},
                 fleet=[dict(ENVELOPE_MODEL["fleet"][0], capacities={"kg": 100})])
    problem = servicemodel.build(model, DEPOTS, STOPS, matrix_for(3))
    assert [o.quantities["kg"] for o in problem.orders] == [5, 12]


def test_service_is_fixed_or_read_from_a_field():
    problem = servicemodel.build(ENVELOPE_MODEL, DEPOTS, STOPS, matrix_for(3))
    assert {o.delivery.service_fixed for o in problem.orders} == {600}

    model = dict(ENVELOPE_MODEL,
                 service={"from_field": "minutes", "scale_seconds": 60})
    scaled = servicemodel.build(model, DEPOTS, STOPS, matrix_for(3))
    assert [o.delivery.service_fixed for o in scaled.orders] == [360, 540]


def test_disjoint_windows_reach_every_stop():
    problem = servicemodel.build(ENVELOPE_MODEL, DEPOTS, STOPS, matrix_for(3))
    for order in problem.orders:
        assert [(w.start, w.end) for w in order.delivery.time_windows] == [
            (28800, 43200), (46800, 61200)]


def test_a_soft_window_carries_its_prices():
    """Coverage the spike lacked: every window it built was hard and free."""
    model = dict(ENVELOPE_MODEL, windows=[
        {"start": 28800, "end": 43200, "hardness": "SOFT",
         "earliness_cost_per_sec": 1, "lateness_cost_per_sec": 12}])
    window = servicemodel.build(model, DEPOTS, STOPS,
                                matrix_for(3)).orders[0].delivery.time_windows[0]
    assert (window.hardness, window.earliness_cost_per_sec,
            window.lateness_cost_per_sec) == ("SOFT", 1, 12)


def test_a_closed_route_returns_and_an_open_one_does_not():
    closed = servicemodel.build(ENVELOPE_MODEL, DEPOTS, STOPS, matrix_for(3))
    assert closed.vehicles[0].end_location_id == "DEPOT"
    assert closed.vehicles[0].open_route is False

    model = dict(ENVELOPE_MODEL, route={"closed": False})
    opened = servicemodel.build(model, DEPOTS, STOPS, matrix_for(3))
    assert opened.vehicles[0].end_location_id is None
    assert opened.vehicles[0].open_route is True


def test_the_duty_cap_is_not_the_shift_window():
    """FR-16 is three components; the spike expressed one."""
    model = dict(ENVELOPE_MODEL,
                 shift={"start": 28800, "end": 57600, "max_duty_seconds": 28800})
    vehicle = servicemodel.build(model, DEPOTS, STOPS, matrix_for(3)).vehicles[0]
    assert (vehicle.shift.start, vehicle.shift.end) == (28800, 57600)
    assert vehicle.max_duration == 28800


def test_a_model_that_cannot_carry_its_own_orders_is_refused():
    """A contradiction is refused at load, not discovered in a plan.

    `Order` refuses a STATUTORY order carrying a prize for the same reason: a
    contradiction written down is worth failing on, because the alternative is
    a plan that looks merely infeasible for no stated cause.
    """
    model = dict(ENVELOPE_MODEL,
                 fleet=[dict(ENVELOPE_MODEL["fleet"][0],
                             capacities={"grams": 100})])
    with pytest.raises(ValueError, match="200"):
        servicemodel.build(model, DEPOTS, STOPS, matrix_for(3))


def test_an_unknown_key_is_refused_at_build_time_too():
    with pytest.raises(ValueError, match="flet"):
        servicemodel.build(dict(ENVELOPE_MODEL, flet=[]), DEPOTS, STOPS,
                           matrix_for(3))
