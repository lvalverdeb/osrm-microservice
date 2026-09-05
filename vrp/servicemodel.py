"""Delivery models: an operation described as data — `T-94`.

Which of the domain's fields a model may set, which it has decided not to, and
the builder that turns a model plus one round's demand into a `Problem`.

A delivery model is everything about an operation *except* today's demand:
which vehicle serves it, what a stop costs in minutes, what a unit weighs, when
the customer will open the door, whether the vehicle comes home. Envelope
delivery is one model; parcel delivery is another. It is not a new domain type
-- `Problem` already expresses all of it -- and this is the factory, held as
JSON so an operation can be reviewed and versioned without a code change.

**The contract is the point of this module.** `COVERS` and `EXCLUDES` say, for
every field of every domain type a model builds, whether the model can set it
and, if not, why not. `tests/vrp/test_service_model.py` checks that against
`dataclasses.fields` itself, so a field added to `Vehicle` fails the suite until
somebody decides about it.

That is not defensive habit. `_vehicle_from_dict` reconstructed 9 of `Vehicle`'s
28 fields until `T-89` and nothing failed: snapshots round-tripped and nineteen
fields were dropped in silence. `vrp.api._window` still drops both soft-window
cost rates today. The throwaway spike for this task managed 8 of 28 and would
have shipped the same defect a third time. Every one of those was a hand-written
field list, which is why this one is machine-checked.

**The JSON schema is derived from the contract**, not written beside it. The
model keys a file may use are computed from `COVERS`, so the schema cannot drift
from the domain model the way a separately-maintained document would.

Two rules the contract cannot express and a reader has to know:

*A model names a computation; it never describes one.* `multi_capacity.cube_of`
is a category-conditional formula, and as data it would be `{"divide_by":
{"Apparel": 8, "default": 60}}` -- the first line of an expression language. A
model says `{"derived": "cube"}` and the code stays code, exactly as
`Vehicle.hos_rules` names `EU-561` rather than carrying its articles.

*A model cannot contain anything that needs a solve to define itself.*
`fleet/tw/sla_windows.py` derives its response targets from percentiles of a
calibration run: the windows do not exist until the round has been solved once.
That is a procedure, and procedures are code.

Three things live here: the contract, the schema derived from it, and the
builder that reads a model and produces a `Problem`. The shipped models are
`models/*.json`, mapped to item categories by `models/categories.json`, and
`tests/test_service_model_examples.py` holds them to the examples they describe
-- a model that cannot rebuild the round somebody wrote by hand is not a model
of it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from vrp.battery import ChargingCurve
from vrp.model import (
    Location,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------
# `COVERS` maps a domain field to the model key that sets it. `EXCLUDES` maps a
# domain field to why a model may not. Between them they must name every field
# of every governed type -- that is what the tests enforce.
#
# The line between them is one question: *is this a fact about the operation, or
# about today?* A motorbike's capacity is the operation. Which driver came on
# duty with six hours already used is today, and a model that carried it would
# be describing one morning rather than a way of working.

COVERS: dict[type, dict[str, str]] = {
    Vehicle: {
        "capacities": "fleet[].capacities",
        "shift": "shift",
        "max_duration": "shift.max_duty_seconds",
        "max_distance": "fleet[].max_distance",
        "skills": "fleet[].skills",
        "fixed_cost": "fleet[].fixed_cost",
        "cost_per_metre": "fleet[].cost_per_metre",
        "cost_per_second": "fleet[].cost_per_second",
        "cost_per_order": "fleet[].cost_per_order",
        "overtime_cost_per_second": "fleet[].overtime_cost_per_second",
        "profile": "fleet[].profile",
        "service_factor_ppt": "fleet[].service_factor_ppt",
        "reload_locations": "fleet[].reload.locations",
        "max_reloads": "fleet[].reload.max",
        "reload_duration": "fleet[].reload.seconds",
        "battery_wh": "fleet[].battery.wh",
        "consumption_wh_per_km": "fleet[].battery.wh_per_km",
        "charging_curve": "fleet[].battery.curve",
        "initial_soc_ppt": "fleet[].battery.initial_ppt",
        "access_class": "fleet[].access_class",
        "gross_weight_kg": "fleet[].gross_weight_kg",
        "hos_rules": "fleet[].hos_rules",
        "open_route": "route.closed",
        "start_location_id": "assignment.depot",
        "end_location_id": "assignment.depot",
    },
    Order: {
        "kind": "order.kind",
        "quantities": "quantity",
        "pickup": "order.kind",
        "delivery": "order.kind",
        "priority_tier": "declinable.priority_tier",
        "prize": "declinable.prize",
        "release_time": "order.release",
        "required_skills": "order.requires",
        "max_ride_time": "order.max_ride_time",
        "priority_source": "order.priority_source",
        "order_class": "order.order_class",
        "incompatible_with": "order.incompatible_with",
    },
    StopSpec: {
        "time_windows": "windows",
        "service_fixed": "service.fixed_seconds",
        "service_per_unit": "service.per_unit",
        "service_per_unit_dimension": "service.per_unit_dimension",
    },
    Location: {
        "dwell_overhead": "service.dwell_overhead",
    },
    TimeWindow: {
        "start": "windows[].start",
        "end": "windows[].end",
        "hardness": "windows[].hardness",
        "earliness_cost_per_sec": "windows[].earliness_cost_per_sec",
        "lateness_cost_per_sec": "windows[].lateness_cost_per_sec",
    },
}

EXCLUDES: dict[type, dict[str, str]] = {
    Vehicle: {
        "id": "Generated from the class name and the depot it starts at. A model "
              "names kinds of vehicle, not individual ones; naming them would make "
              "a model describe a garage rather than an operation.",
        "charger_locations": "Which sites have chargers is geography, and arrives "
                             "with the locations. A model that carried it would go "
                             "stale the day somebody installs one.",
        "initial_state": "Hours already consumed when a driver came on duty. That "
                         "is a fact about this morning, not about the operation, "
                         "and SDD 6.4 makes it a per-run input.",
    },
    Order: {
        "id": "The delivery record's own identifier. A model describes how work is "
              "served, never which work exists.",
    },
    StopSpec: {
        "location_id": "The delivery's own site, which arrives with the demand. A "
                       "model that named locations would be a round rather than a "
                       "way of running rounds.",
    },
    Location: {
        "id": "Site identity arrives with the demand, not with the operation.",
        "lat": "Geography arrives with the demand, not with the operation.",
        "lon": "Geography arrives with the demand, not with the operation.",
        "matrix_index": "Assigned while building the matrix for one round; it means "
                        "nothing outside that round.",
        "dock_capacity": "How many bays a depot has is a property of that depot, "
                         "and FR-19 makes it site data rather than policy.",
        "inventory": "What a depot holds changes daily. FR-31 makes it a per-run "
                     "input, and a model carrying it would be yesterday's stock.",
        "access_classes": "Which vehicles a site admits is a fact about the site. "
                          "The model's half of FR-11 is the vehicle's access_class, "
                          "which is covered.",
        "max_vehicle_kg": "A bridge or a yard's weight limit belongs to the place. "
                          "The model's half is the vehicle's gross_weight_kg.",
    },
    TimeWindow: {},
}


def _paths() -> set[str]:
    """Every model key the contract implies, as dotted paths."""
    return {path for mapping in COVERS.values() for path in mapping.values()}


def known_keys() -> set[str]:
    """Top-level keys a model file may carry, derived from `COVERS`.

    Derived rather than declared, so the schema cannot drift from the domain
    model. A separately-maintained key list is the same hazard as a
    hand-written field list, one indirection further away.
    """
    return {path.split(".")[0].removesuffix("[]") for path in _paths()}


# Keys a model carries that set no domain field directly: its identity, what it
# applies to, and how it composes. Declared here because `known_keys` derives
# only the fields, and a file needs these to be a file.
STRUCTURAL_KEYS = frozenset({"name", "applies_to", "problem_id", "base", "tunable"})


def validate_keys(raw: dict[str, Any]) -> list[str]:
    """Complaints about a model file's top-level keys.

    Returns:
        One message per unrecognised key, empty when the file is clean. An
        unknown key is an error rather than something ignored: a typo that is
        ignored is a setting that silently did not apply, which is the failure
        mode this module exists to refuse.
    """
    allowed = known_keys() | STRUCTURAL_KEYS
    return [f"unknown model key {key!r}; the contract knows "
            f"{', '.join(sorted(allowed))}"
            for key in sorted(raw) if key not in allowed]


# --------------------------------------------------------------------------
# Named strategies and computations
# --------------------------------------------------------------------------
# A model *names* one of these; it never describes one. The registries are the
# whole of the extension mechanism, and deliberately so: the moment a model
# needs behaviour that is not in one of them, that is a code change with a test
# rather than an edit to a data file.

ROUNDERS: dict[str, Callable[[float], int]] = {"up": math.ceil, "nearest": round}

# How the caller chose its depots and stops. `vrp` does not implement these --
# `examples/src/dataset.py` does -- but the name is validated here so a model
# cannot ship a strategy nothing can resolve.
KNOWN_DEPOT_STRATEGIES = frozenset({
    "nearest", "spread", "furthest", "around_each_depot", "by_province",
    "busiest_depot", "cluster_with_outliers", "contested",
})

# Shipped computations a model may name for a quantity it cannot read off a
# field. Empty until one is needed: `multi_capacity.cube_of` is the candidate,
# and it lives in an example rather than here.
DERIVED: dict[str, Callable[[dict[str, Any]], int]] = {}


def depot_strategy(model: dict[str, Any]) -> str:
    """Which slicing strategy this model expects its caller to have used."""
    return model["assignment"]["depot"]


def _quantities(model: dict[str, Any], record: dict[str, Any]) -> dict[str, int]:
    specs = model["quantity"]
    return {spec["dimension"]: _one_quantity(spec, record)
            for spec in ([specs] if isinstance(specs, dict) else specs)}


def _one_quantity(spec: dict[str, Any], record: dict[str, Any]) -> int:
    if "fixed" in spec:
        return spec["fixed"]
    if "derived" in spec:
        return DERIVED[spec["derived"]](record)
    rounder = ROUNDERS[spec.get("round", "nearest")]
    return rounder(record[spec["from_field"]])


def _service_seconds(spec: dict[str, Any], record: dict[str, Any]) -> int:
    if "fixed_seconds" in spec:
        return spec["fixed_seconds"]
    return record[spec["from_field"]] * spec["scale_seconds"]


def _windows(specs: Sequence[dict[str, Any]]) -> tuple[TimeWindow, ...]:
    return tuple(TimeWindow(**spec) for spec in specs)


def _stop(model: dict[str, Any], record: dict[str, Any]) -> StopSpec:
    service = model["service"]
    return StopSpec(
        location_id=record["id"],
        time_windows=_windows(model["windows"]),
        service_fixed=_service_seconds(service, record),
        service_per_unit=service.get("per_unit", 0),
        service_per_unit_dimension=service.get("per_unit_dimension"))


def _order(model: dict[str, Any], record: dict[str, Any]) -> Order:
    declinable = model.get("declinable", {})
    spec = model.get("order", {})
    stop = _stop(model, record)
    kind = spec.get("kind", "JOB")
    collecting = spec.get("collects", False)
    return Order(
        id=record["id"], kind=kind,
        quantities=_quantities(model, record),
        pickup=stop if collecting else None,
        delivery=None if collecting else stop,
        priority_tier=declinable.get("priority_tier", 0),
        prize=declinable.get("prize", 0),
        release_time=spec.get("release", 0),
        required_skills=frozenset(spec.get("requires", ())),
        max_ride_time=spec.get("max_ride_time"),
        priority_source=spec.get("priority_source", "COMMERCIAL"),
        order_class=spec.get("order_class"),
        incompatible_with=frozenset(spec.get("incompatible_with", ())))


def _battery(spec: dict[str, Any]) -> dict[str, Any]:
    """FR-20's fields, absent together or present together."""
    if not spec:
        return {}
    curve = spec.get("curve")
    return {
        "battery_wh": spec["wh"],
        "consumption_wh_per_km": spec["wh_per_km"],
        "initial_soc_ppt": spec.get("initial_ppt", 1000),
        "charging_curve": None if curve is None else ChargingCurve(
            bands=tuple(tuple(band) for band in curve)),
    }


def _vehicle(spec: dict[str, Any], model: dict[str, Any], depot: dict[str, Any],
             number: int) -> Vehicle:
    shift = model["shift"]
    closed = model["route"]["closed"]
    reload_spec = spec.get("reload", {})
    return Vehicle(
        id=spec["id"].format(n=number, **{"class": spec["class"],
                                          "depot": depot["id"]}),
        capacities=dict(spec["capacities"]),
        shift=TimeWindow(start=shift["start"], end=shift["end"]),
        max_duration=shift.get("max_duty_seconds"),
        max_distance=spec.get("max_distance"),
        skills=frozenset(spec.get("skills", ())),
        fixed_cost=spec.get("fixed_cost", 0),
        cost_per_metre=spec.get("cost_per_metre", 0),
        cost_per_second=spec.get("cost_per_second", 0),
        cost_per_order=spec.get("cost_per_order", 0),
        overtime_cost_per_second=spec.get("overtime_cost_per_second", 0),
        profile=spec.get("profile", "driving"),
        service_factor_ppt=spec.get("service_factor_ppt", 1000),
        reload_locations=frozenset(reload_spec.get("locations", ())),
        max_reloads=reload_spec.get("max", 0),
        reload_duration=reload_spec.get("seconds", 0),
        access_class=spec.get("access_class"),
        gross_weight_kg=spec.get("gross_weight_kg"),
        hos_rules=spec.get("hos_rules"),
        start_location_id=depot["id"],
        end_location_id=depot["id"] if closed else None,
        open_route=not closed,
        **_battery(spec.get("battery", {})))


def _refuse_contradictions(model: dict[str, Any], orders: Sequence[Order],
                           vehicles: Sequence[Vehicle]) -> None:
    """A load no vehicle in the fleet can carry is a contradiction written down.

    Refused here rather than left to a solver, for the reason `Order` refuses a
    STATUTORY order carrying a prize: the alternative is a plan that comes back
    infeasible with no stated cause, and a model file nobody suspects.
    """
    for order in orders:
        for dimension, amount in order.quantities.items():
            biggest = max((v.capacities.get(dimension, 0) for v in vehicles),
                          default=0)
            if amount > biggest:
                raise ValueError(
                    f"model {model['name']!r}: order {order.id} needs {amount} "
                    f"{dimension} and the largest vehicle carries {biggest}")


def build(model: dict[str, Any], depots: Sequence[dict[str, Any]],
          deliveries: Sequence[dict[str, Any]],
          matrix: TravelMatrix) -> Problem:
    """Turn a model and one round's demand into a `Problem`.

    Args:
        model: a loaded model file.
        depots: records carrying `id`, `lat`, `lon`.
        deliveries: records carrying `id`, `lat`, `lon`, plus whatever fields
            the model's `from_field` keys name. The model is the adapter
            between a data source and the domain, which is why nothing here
            knows a corpus's spelling.
        matrix: the pinned travel matrix, depots first.

    Returns:
        The problem the model describes over that demand.

    Raises:
        ValueError: if the file carries a key the contract does not know, names
            an unknown depot strategy, or describes a fleet that cannot carry
            its own orders.
    """
    complaints = validate_keys(model)
    if complaints:
        raise ValueError("; ".join(complaints))
    if depot_strategy(model) not in KNOWN_DEPOT_STRATEGIES:
        raise ValueError(
            f"unknown depot strategy {depot_strategy(model)!r}; shipped: "
            f"{', '.join(sorted(KNOWN_DEPOT_STRATEGIES))}")

    dwell = model["service"].get("dwell_overhead", 0)
    locations = [Location(id=d["id"], lat=d["lat"], lon=d["lon"], matrix_index=i)
                 for i, d in enumerate(depots)]
    locations += [Location(id=r["id"], lat=r["lat"], lon=r["lon"],
                           matrix_index=len(depots) + offset,
                           dwell_overhead=dwell)
                  for offset, r in enumerate(deliveries)]
    orders = tuple(_order(model, record) for record in deliveries)
    vehicles = tuple(
        _vehicle(spec, model, depot, n)
        for depot in depots
        for spec in model["fleet"]
        for n in range(1, spec["per_depot"] + 1))
    _refuse_contradictions(model, orders, vehicles)
    return Problem(id=model["problem_id"], locations=tuple(locations),
                   orders=orders, vehicles=vehicles, matrix=matrix)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------
# In-repo JSON, versioned with the code. Not fetched at runtime: a model that
# can change without a release is a model that can change without a review, and
# `T-96`'s gate is what makes an edit safe rather than merely possible.

MODELS = Path(__file__).resolve().parent.parent / "models"


def model_for(name: str) -> dict[str, Any]:
    """One shipped model, by name. `hos.rules_for`'s shape, for the same reason.

    Raises:
        ValueError: if no model of that name ships, naming the ones that do
            rather than leaving a caller to list the directory.
    """
    path = MODELS / f"{name}.json"
    if not path.exists():
        shipped = sorted(f.stem for f in MODELS.glob("*.json")
                         if f.name != "categories.json")
        raise ValueError(f"unknown delivery model {name!r}; "
                         f"shipped: {', '.join(shipped)}")
    return json.loads(path.read_text())


def model_for_category(category: str) -> dict[str, Any]:
    """The model an item category is served by. One global map, `T-94`.

    Global because a category selects a model everywhere or the map stops being
    a map: a per-depot override is a deployment concern, and belongs to the
    overlay rules rather than to this lookup.

    Raises:
        ValueError: if the category is not mapped. Silence would mean serving
            unknown freight by whichever model happened to be first.
    """
    mapping = json.loads((MODELS / "categories.json").read_text())
    if category not in mapping:
        raise ValueError(f"no delivery model for category {category!r}; "
                         f"mapped: {', '.join(sorted(mapping))}")
    return model_for(mapping[category])
