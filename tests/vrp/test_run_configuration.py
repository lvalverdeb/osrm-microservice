"""`T-95` — the run configuration a delivery model does not own.

`T-94` describes an operation: the vehicle, the unit, the service time, the
windows. None of that says how the plan was *chosen*. `ObjectiveSpec`'s mode
and rates, the engine, its budget and its seed are not in `Problem`, so a model
that fixes the fleet has not pinned the plan -- two runs of one model can order
two plans differently and both be right.

So the run configuration is a sibling of the model, resolved separately and
sealed into the snapshot beside it. Kept apart on purpose: merging them is how
a model grows until it can express a replan policy, which is the thing the plan
for this work says explicitly it must not do.
"""

from __future__ import annotations

import pytest

from vrp import servicemodel, snapshot
from vrp.model import Location, Problem, TravelMatrix, Vehicle
from vrp.objective import Mode, ObjectiveSpec, Tier, TierValues, compare

MODEL = {
    "name": "test-envelopes",
    "applies_to": ["Documents"],
    "problem_id": "envelopes",
    "quantity": {"dimension": "grams", "fixed": 200},
    "service": {"fixed_seconds": 600},
    "windows": [{"start": 28800, "end": 57600}],
    "shift": {"start": 28800, "end": 57600},
    "route": {"closed": True},
    "assignment": {"depot": "nearest"},
    "fleet": [{"class": "COURIER", "id": "COURIER-{n}", "per_depot": 2,
               "capacities": {"grams": 25000}}],
    "run": {"engine": "pyvrp", "budget": 1200, "seed": 0,
            "objective": {"mode": "MIN_COST", "vehicle_fixed_cost": 50000,
                          "cost_per_metre": 1, "cost_per_second": 0}},
}


def test_a_run_configuration_resolves_from_its_own_section():
    run = servicemodel.run_config(MODEL)
    assert (run.engine, run.budget, run.seed) == ("pyvrp", 1200, 0)
    assert run.objective == ObjectiveSpec(mode=Mode.MIN_COST,
                                          vehicle_fixed_cost=50000,
                                          cost_per_metre=1, cost_per_second=0)


def test_a_model_that_pins_no_objective_is_refused():
    """A fleet without an objective is half a plan.

    Not defaulted, because the default would be invisible and load-bearing: two
    people reading the same model file would disagree about which plan it
    describes, and neither would be wrong.
    """
    with pytest.raises(ValueError, match="run"):
        servicemodel.run_config({k: v for k, v in MODEL.items() if k != "run"})


def test_an_unknown_engine_names_the_ones_that_ship():
    with pytest.raises(ValueError, match="pyvrp"):
        servicemodel.run_config(
            dict(MODEL, run=dict(MODEL["run"], engine="cuopt")))


def test_an_unknown_objective_mode_names_the_ones_that_ship():
    with pytest.raises(ValueError, match="MIN_COST"):
        servicemodel.run_config(
            dict(MODEL, run=dict(MODEL["run"],
                                 objective=dict(MODEL["run"]["objective"],
                                                mode="CHEAPEST"))))


def test_the_mode_changes_which_plan_wins():
    """The acceptance condition: the objective is not decoration.

    One plan uses fewer vehicles and drives further; the other is the reverse.
    `MIN_VEHICLES` ranks FLEET on a level of its own, so the small fleet wins.
    `MIN_COST` merges FLEET with OPERATING, so the total decides and the answer
    flips. Same problem, same demand, two models -- two plans, both right.
    """
    frugal = TierValues({Tier.FLEET: 50_000, Tier.OPERATING: 90_000})
    quick = TierValues({Tier.FLEET: 100_000, Tier.OPERATING: 20_000})

    by_vehicles = servicemodel.run_config(
        dict(MODEL, run=dict(MODEL["run"],
                             objective=dict(MODEL["run"]["objective"],
                                            mode="MIN_VEHICLES")))).objective
    by_cost = servicemodel.run_config(MODEL).objective

    assert compare(frugal, quick, by_vehicles) < 0, "fewer vehicles should win"
    assert compare(frugal, quick, by_cost) > 0, "the cheaper total should win"


def test_the_sealed_configuration_names_the_model_and_its_digest():
    """`NFR-08`: a plan is replayable from its snapshot, or it is not a record.

    The digest is over the *resolved* model rather than the file, so two files
    meaning the same thing seal alike and one file whose meaning changed does
    not.
    """
    config = servicemodel.as_config(MODEL)
    assert config["model"] == "test-envelopes"
    assert config["engine"] == "pyvrp"
    assert config["budget"] == 1200
    assert config["seed"] == 0
    assert config["objective"]["mode"] == "MIN_COST"
    assert len(config["model_digest"]) == 64


def test_a_changed_model_seals_differently():
    heavier = dict(MODEL, quantity={"dimension": "grams", "fixed": 250})
    assert (servicemodel.as_config(MODEL)["model_digest"]
            != servicemodel.as_config(heavier)["model_digest"])


def test_key_order_does_not_change_the_digest():
    """Two files meaning the same thing must seal alike."""
    shuffled = dict(reversed(list(MODEL.items())))
    assert (servicemodel.as_config(MODEL)["model_digest"]
            == servicemodel.as_config(shuffled)["model_digest"])


def a_problem() -> Problem:
    rows = ((0, 60), (60, 0))
    return Problem(
        id="p",
        locations=(Location(id="D", lat=9.9, lon=-84.1, matrix_index=0),
                   Location(id="A", lat=9.95, lon=-84.05, matrix_index=1)),
        orders=(),
        vehicles=(Vehicle(id="V", capacities={"kg": 10},
                          shift=servicemodel.TimeWindow(start=0, end=3600),
                          start_location_id="D"),),
        matrix=TravelMatrix(version="m", durations=rows, distances=rows))


def test_a_snapshot_seals_the_model_beside_the_problem():
    """`NFR-08`: the plan is replayable from its snapshot, or it is not a record."""
    sealed = snapshot.capture(a_problem(), servicemodel.as_config(MODEL))
    assert sealed.config["model"] == "test-envelopes"
    assert sealed.config["engine"] == "pyvrp"
    assert len(sealed.digest) == 64


def test_the_same_model_and_problem_seal_identically():
    first = snapshot.capture(a_problem(), servicemodel.as_config(MODEL))
    again = snapshot.capture(a_problem(), servicemodel.as_config(MODEL))
    assert first.digest == again.digest


def test_a_different_seed_is_a_different_plan_and_seals_so():
    """The same instance at a different seed is a different plan (CON-4)."""
    base = snapshot.capture(a_problem(), servicemodel.as_config(MODEL))
    reseeded = snapshot.capture(
        a_problem(),
        servicemodel.as_config(dict(MODEL, run=dict(MODEL["run"], seed=7))))
    assert base.digest != reseeded.digest


def test_the_objective_ranks_plans_but_does_not_steer_the_search():
    """A limitation, pinned so nobody reads more into `run.objective`.

    No solver adapter accepts an `ObjectiveSpec`: `vrp.solve.pyvrp_adapter` and
    `vrp.solve.ortools_adapter` take a problem, a budget and a seed, and the
    spec is consumed by `vrp.objective`, `vrp.evaluator` and `vrp.allocate` --
    all of which score or compare a plan that already exists. So a model's mode
    decides which of two plans wins, not which plan the engine finds.
    """
    import inspect

    from vrp.solve import ortools_adapter, pyvrp_adapter
    for adapter in (pyvrp_adapter, ortools_adapter):
        parameters = inspect.signature(adapter.solve).parameters
        assert "objective" not in parameters and "spec" not in parameters, (
            f"{adapter.__name__}.solve now takes an objective -- the run "
            "configuration can steer the search, and this test and the "
            "docstring above it are out of date")
