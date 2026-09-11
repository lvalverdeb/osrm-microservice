"""`T-96` — the gate that makes model-driven configuration safe.

Moving configuration from code to JSON moves a class of failure from review
time to run time. A bad constant used to need a code change, a test and a
review; as a model file it needs an edit. This is what buys that back.

The failure it exists to catch: a model can be valid JSON, satisfy `T-94`'s
contract, build a legal `Problem`, and still be operationally impossible. A
25 kg motorbike against 40 kg parcels is infeasible every day, and nothing
upstream of a solve can say so.

Two things it must report that no existing measure does. **Only
verifier-accepted plans score** -- asked to serve 120 envelopes with one
courier, PyVRP returns its best infeasible attempt with arrivals clamped to
noon, and a naive count reports 120 letters delivered by a rider who could not
have managed 40. And **which constraint bound**, because "three couriers" is
useless without "because the shift ran out, not the satchel": the binding
constraint is the number that says which parameter to adjust, and nothing
today reports it for a plan that succeeded.
"""

from __future__ import annotations

import json

import pytest

from vrp import modelcheck, servicemodel
from vrp.model import TravelMatrix

DEPOTS = [{"id": "D", "lat": 9.9400, "lon": -84.0500}]
STOPS = [
    {"id": "A", "lat": 9.9410, "lon": -84.0510, "kg": 6},
    {"id": "B", "lat": 9.9420, "lon": -84.0520, "kg": 6},
    {"id": "C", "lat": 9.9430, "lon": -84.0530, "kg": 6},
]

BASE = {
    "name": "base",
    "applies_to": ["Documents"],
    "problem_id": "gate",
    "quantity": {"dimension": "kg", "from_field": "kg", "round": "up"},
    "service": {"fixed_seconds": 600},
    "windows": [{"start": 28800, "end": 57600}],
    "shift": {"start": 28800, "end": 57600},
    "route": {"closed": True},
    "assignment": {"depot": "nearest"},
    "fleet": [{"class": "VAN", "id": "VAN-{n}", "per_depot": 1,
               "capacities": {"kg": 100}}],
    "run": {"engine": "pyvrp", "budget": 200, "seed": 0,
            "objective": {"mode": "MIN_COST", "vehicle_fixed_cost": 50000,
                          "cost_per_metre": 1, "cost_per_second": 0}},
}


def matrix(size: int, seconds: int = 300) -> TravelMatrix:
    rows = tuple(tuple(0 if i == j else seconds for j in range(size))
                 for i in range(size))
    return TravelMatrix(version="m", durations=rows, distances=rows)


def check(model: dict) -> modelcheck.ModelResult:
    return modelcheck.check(model, DEPOTS, STOPS, matrix(len(STOPS) + 1))


def test_a_workable_model_passes_and_says_what_bound():
    result = check(BASE)
    assert result.accepted
    assert result.unassigned == 0
    assert result.binding is not None


# Each of the next two leaves spare vehicles on purpose. A fleet with every van
# deployed has zero slack and wins on any ranking, which is a true answer -- you
# ran out of vans -- and a useless one for isolating anything else.


def test_a_tight_shift_binds_on_time_not_on_capacity():
    """Three ten-minute calls and five-minute legs against a one-hour shift."""
    tight = dict(BASE, shift={"start": 28800, "end": 28800 + 3600},
                 fleet=[dict(BASE["fleet"][0], per_depot=4)])
    result = check(tight)
    assert result.binding.constraint == "shift", result.binding


def test_a_small_box_binds_on_capacity_not_on_time():
    """The same round, a whole day to do it in, and a van that barely carries it."""
    small = dict(BASE, fleet=[dict(BASE["fleet"][0], per_depot=8,
                                   capacities={"kg": 7})])
    result = check(small)
    assert result.binding.constraint == "capacity:kg", result.binding


def test_an_impossible_model_never_scores():
    """The trap `E-94` walked into: a solver's stop count is what it attempted.

    One van, a one-kilogram box, and eighteen kilograms of work. `T-94` refuses
    this at load, so the gate reports the refusal rather than a cost.
    """
    impossible = dict(BASE, fleet=[dict(BASE["fleet"][0],
                                        capacities={"kg": 1})])
    result = check(impossible)
    assert not result.accepted
    assert result.cost is None
    assert result.binding is None
    assert "1" in result.refused


def test_a_plan_the_verifier_rejects_scores_nothing():
    """The gate's whole reason for existing, and it needs a *solved* refusal.

    `test_an_impossible_model_never_scores` is refused at load, before any
    solver runs, so it says nothing about this. Here the model builds, the
    solver runs, and comes back with its best infeasible attempt: a one-minute
    hard window that no route can meet, on orders carrying no prize and so not
    declinable. PyVRP returns arrivals clamped to the window it could not
    reach, and a gate that scored that would rank a fantasy.
    """
    unreachable = dict(BASE, windows=[{"start": 28800, "end": 28860}])
    result = check(unreachable)
    assert not result.accepted, "an infeasible plan must not be accepted"
    assert result.cost is None, "a rejected plan must not carry a cost"
    assert result.binding is None, "a rejected plan has no binding constraint"
    assert result.refused


def test_attainment_is_reported_rather_than_an_assignment_rate():
    """`MixResult.service_level` counts placement, so a plan served two hours
    late scores perfectly. The gate reads `window_attainment` instead."""
    result = check(BASE)
    assert result.attainment.promised == len(STOPS)
    assert result.attainment.attained_ppt == 1000


def test_a_model_serving_late_loses_to_one_serving_on_time():
    late = dict(BASE, windows=[{"start": 28800, "end": 28800 + 60,
                                "hardness": "SOFT",
                                "earliness_cost_per_sec": 0,
                                "lateness_cost_per_sec": 1}])
    punctual, tardy = check(BASE), check(late)
    assert punctual.attainment.attained_ppt > tardy.attainment.attained_ppt


def test_comparing_models_runs_them_over_identical_demand():
    roomy = dict(BASE, name="roomy")
    cramped = dict(BASE, name="cramped",
                   fleet=[dict(BASE["fleet"][0], per_depot=8,
                               capacities={"kg": 7})])
    results = modelcheck.compare([roomy, cramped], DEPOTS, STOPS,
                                 matrix(len(STOPS) + 1))
    assert [r.model for r in results] == ["roomy", "cramped"]
    assert {r.binding.constraint for r in results} == {"shift", "capacity:kg"} \
        or all(r.accepted for r in results)


def test_the_gate_refuses_a_model_that_cannot_serve_the_round():
    impossible = dict(BASE, fleet=[dict(BASE["fleet"][0],
                                        capacities={"kg": 1})])
    assert modelcheck.passes(check(BASE))
    assert not modelcheck.passes(check(impossible))


def test_structural_checks_need_no_solve():
    """Cheap enough for a pre-commit hook, unlike everything above."""
    assert modelcheck.structural(BASE) == []
    complaints = modelcheck.structural(dict(BASE, flet=[]))
    assert any("flet" in c for c in complaints)
    missing_run = {k: v for k, v in BASE.items() if k != "run"}
    assert any("run" in c for c in modelcheck.structural(missing_run))


# `T-97` closes what `T-96` left open: the gate runs per *variant*. A validated
# master shipping six unvalidated variants is the failure composition would
# otherwise introduce -- the master is the file under review, and the variants
# are where the values that break a round actually live.


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A models directory holding a master and its variants."""
    monkeypatch.setattr(servicemodel, "MODELS", tmp_path)

    def write(name: str, body: dict) -> None:
        (tmp_path / f"{name}.json").write_text(json.dumps(body))

    write("master", dict(BASE, name="master",
                         tunable=["fleet.VAN.per_depot",
                                  "fleet.VAN.capacities"]))
    return write


def test_the_gate_checks_the_resolved_variant_not_its_master(library):
    """The master passes; the variant that shrinks the van does not."""
    library("branch", {"name": "branch", "base": "master",
                       "set": {"fleet.VAN.capacities": {"kg": 1}}})
    variant = servicemodel.model_for("branch")

    assert modelcheck.structural(variant) == []
    assert modelcheck.passes(check(servicemodel.model_for("master")))
    assert not modelcheck.passes(check(variant))


def test_a_variants_failure_is_attributed_to_one_overlay_field(library):
    """Reverting each overlay field in turn -- bounded because depth is one."""
    library("branch", {"name": "branch", "base": "master",
                       "set": {"fleet.VAN.per_depot": 2,
                               "fleet.VAN.capacities": {"kg": 1}}})
    variant = servicemodel.model_for("branch")

    culprit = modelcheck.attribute(variant, DEPOTS, STOPS,
                                   matrix(len(STOPS) + 1))
    assert culprit == "fleet.VAN.capacities"


def test_a_variant_that_works_has_no_field_to_blame(library):
    library("branch", {"name": "branch", "base": "master",
                       "set": {"fleet.VAN.per_depot": 2}})
    variant = servicemodel.model_for("branch")

    assert modelcheck.attribute(variant, DEPOTS, STOPS,
                                matrix(len(STOPS) + 1)) is None


def test_the_gate_can_enumerate_what_ships_variants_included(library):
    """A pre-commit hook needs the list, or it checks only what it knows."""
    library("branch", {"name": "branch", "base": "master",
                       "set": {"fleet.VAN.per_depot": 2}})
    assert servicemodel.shipped() == ["branch", "master"]
