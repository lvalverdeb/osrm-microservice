"""The coverage contract for a delivery model.

Which of the domain's fields a model may set, and which it has decided not to.
This is the contract and the schema derived from it; the builder that turns a
model into a `Problem` is not here yet, and is the rest of `T-94`.

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

Building the `Problem` is the next step of `T-94` and deliberately not here yet;
this module is the contract, the schema derived from it, and the loader that
refuses a file the contract does not recognise.
"""

from __future__ import annotations

from typing import Any

from vrp.model import Location, Order, StopSpec, TimeWindow, Vehicle

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
