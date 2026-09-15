"""The gate a delivery model passes before it ships — `T-96`.

Moving configuration from code to JSON moves a class of failure from review
time to run time. A bad constant used to need a code change, a test and a
review; as a model file it needs an edit. This module is what buys that back,
which is why it is a gate rather than a report: no model reaches `models/`
without passing it.

The failure it exists to catch is not a malformed file. `T-94`'s contract
already refuses those. It is the model that is valid JSON, satisfies the
contract, builds a legal `Problem`, and is still operationally impossible --
a 25 kg motorbike against 40 kg parcels is infeasible every day, and nothing
upstream of a solve can say so.

Two things are reported here that no existing measure gives.

**Only verifier-accepted plans score.** Asked to serve 120 envelopes with one
courier, PyVRP returns its best *infeasible* attempt with every arrival clamped
to noon, and a naive count reports 120 letters delivered by a rider who could
not have managed forty. `fleet/tw/multiple_windows.py` states the rule this
follows: a solver's stop count is what it attempted, not what is achievable.

**Which constraint bound.** "Three couriers" is useless without "because the
shift ran out, not the satchel". The binding constraint is the number that says
which parameter to adjust, and `vrp.diagnose` does not answer it -- that
explains why an order was rejected pre-flight (`T-14`), not what was tightest
in a plan that succeeded.

Attainment rather than an assignment rate: `scenarios.MixResult.service_level`
counts placement, so a model that serves everything two hours late scores
perfectly. `evaluator.window_attainment` is the measure that does not.

**Per variant, not per master** (`T-97`). Everything here resolves the model
first, so a variant carrying only `base` and `set` is checked as what it will
actually run, and composition's own rules -- disjoint sections, no chains, an
overlay inside its tunable surface -- are refusals the gate reports like any
other. `attribute` then names the one overlay field responsible, by reverting
each in turn. A validated master shipping six unvalidated variants is the
failure this exists to prevent. See `docs/planning/DELIVERY_MODELS_PLAN.md`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from vrp import servicemodel
from vrp.evaluator import Attainment, ObjectiveWeights, evaluate, window_attainment
from vrp.model import Problem, TravelMatrix
from vrp.verify import verify

FULL = 1000


@dataclass(frozen=True)
class Binding:
    """The tightest constraint in a plan, and how much room it had left.

    `slack_ppt` is parts per thousand of the resource still unused, so the
    constraint with the least is the one to adjust. Comparable across
    constraints precisely because it is a share rather than a quantity: eleven
    spare minutes and eleven spare kilograms are not otherwise comparable.
    """

    constraint: str
    slack_ppt: int


@dataclass(frozen=True)
class ModelResult:
    """What the gate found. `refused` is set when nothing else could be."""

    model: str
    accepted: bool
    status: str
    attainment: Attainment
    vehicles_used: int
    unassigned: int
    cost: int | None = None
    binding: Binding | None = None
    refused: str = ""


def structural(model: dict[str, Any]) -> list[str]:
    """Everything wrong with a model that needs no solve to see.

    Cheap enough for a pre-commit hook, which is where it belongs: the checks
    below run in microseconds while everything else in this module runs a
    solver over a scenario set.
    """
    try:
        model = servicemodel.resolve_model(model).model
    except (ValueError, KeyError) as failure:
        return [str(failure)]
    complaints = list(servicemodel.validate_keys(model))
    complaints += servicemodel.missing_sections(model)
    try:
        servicemodel.run_config(model)
    except (ValueError, KeyError) as failure:
        complaints.append(str(failure))
    return complaints


def _time_slack(problem: Problem, solution) -> Binding | None:
    """How much of the shift the longest route left unused."""
    worst = None
    for route in solution.routes:
        if not route.steps:
            continue
        shift = problem.vehicle(route.vehicle_id).shift
        span = shift.end - shift.start
        if span <= 0:
            continue
        spare = max(0, shift.end - route.steps[-1].arrival)
        share = spare * FULL // span
        worst = share if worst is None else min(worst, share)
    return None if worst is None else Binding("shift", worst)


def _capacity_slack(problem: Problem, solution) -> list[Binding]:
    """Per dimension, how much of the fullest vehicle was left empty."""
    tightest: dict[str, int] = {}
    for route in solution.routes:
        vehicle = problem.vehicle(route.vehicle_id)
        for dimension, limit in vehicle.capacities.items():
            if limit <= 0:
                continue
            peak = max((step.load_after.get(dimension, 0)
                        for step in route.steps), default=0)
            share = max(0, (limit - peak)) * FULL // limit
            tightest[dimension] = min(tightest.get(dimension, FULL), share)
    return [Binding(f"capacity:{name}", share)
            for name, share in sorted(tightest.items())]


def _fleet_slack(problem: Problem, used: int) -> Binding | None:
    available = len(problem.vehicles)
    if available <= 0:
        return None
    return Binding("fleet", (available - used) * FULL // available)


def binding_constraint(problem: Problem, solution, used: int) -> Binding | None:
    """Whichever constraint had the least room left.

    Slack is a share of each constraint's own budget, so they can be ranked
    against one another. A plan that finished with 2% of its shift spare and
    40% of its box empty is a plan whose answer changes when the day gets
    longer, and not when the box does.
    """
    candidates = [b for b in (_time_slack(problem, solution),
                              _fleet_slack(problem, used),
                              *_capacity_slack(problem, solution)) if b]
    return min(candidates, key=lambda b: b.slack_ppt) if candidates else None


def check(model: dict[str, Any], depots: Sequence[dict[str, Any]],
          deliveries: Sequence[dict[str, Any]], matrix: TravelMatrix,
          solve=None) -> ModelResult:
    """Run one model over one round and report what the gate found.

    Args:
        model: a loaded model file.
        depots: records carrying `id`, `lat`, `lon`.
        deliveries: the demand, as `servicemodel.build` expects it.
        matrix: the pinned travel matrix.
        solve: the solver, defaulting to the engine the model's `run` section
            names. Injectable so a caller can gate against a stub.

    Returns:
        A `ModelResult`. `cost` and `binding` are `None` unless the verifier
        accepted the plan -- scoring a rejected plan is how a comparison ends
        up ranking fantasies.
    """
    complaints = structural(model)
    if complaints:
        return ModelResult(model=model.get("name", "?"), accepted=False,
                           status="REFUSED", attainment=Attainment(),
                           vehicles_used=0, unassigned=0,
                           refused="; ".join(complaints))
    model = servicemodel.resolve_model(model).model
    run = servicemodel.run_config(model)
    try:
        problem = servicemodel.build(model, depots, deliveries, matrix)
    except ValueError as failure:
        return ModelResult(model=model["name"], accepted=False,
                           status="REFUSED", attainment=Attainment(),
                           vehicles_used=0, unassigned=0, refused=str(failure))

    solution = (solve or _engine(run.engine))(problem, run.budget, run.seed)
    used = sum(1 for r in solution.routes if any(s.order_id for s in r.steps))
    accepted = verify(problem, solution).ok and solution.status == "FEASIBLE"
    assignment = {r.vehicle_id: [s.order_id for s in r.steps if s.order_id]
                  for r in solution.routes}
    timeline = tuple(step for route in solution.routes for step in route.steps)

    if not accepted:
        return ModelResult(model=model["name"], accepted=False,
                           status=solution.status,
                           attainment=window_attainment(problem, timeline),
                           vehicles_used=used,
                           unassigned=len(solution.unassigned),
                           refused="the verifier rejected the plan")

    weights = ObjectiveWeights(per_metre=run.objective.cost_per_metre,
                               per_second=run.objective.cost_per_second,
                               per_vehicle=run.objective.vehicle_fixed_cost)
    scored = evaluate(problem, assignment, weights)
    canonical = tuple(step for line in scored.timelines.values()
                      for step in line)
    return ModelResult(
        model=model["name"], accepted=True, status=solution.status,
        attainment=window_attainment(problem, canonical),
        vehicles_used=used, unassigned=len(solution.unassigned),
        cost=scored.total,
        binding=binding_constraint(problem, solution, used))


def _engine(name: str):
    """Resolve the named engine to a `(problem, budget, seed)` callable.

    The adapters disagree about what the budget is called -- PyVRP takes
    `iterations`, OR-Tools `solutions` -- which is why `T-95`'s models name a
    quantity and this maps it.
    """
    if name == "pyvrp":
        from vrp.solve.pyvrp_adapter import solve as pyvrp
        return lambda p, budget, seed: pyvrp(p, iterations=budget, seed=seed)
    from vrp.solve.ortools_adapter import solve as ortools
    return lambda p, budget, seed: ortools(p, solutions=budget, seed=seed)


def passes(result: ModelResult) -> bool:
    """Whether this model may ship: a legal plan that serves the whole round."""
    return result.accepted and result.unassigned == 0


def attribute(variant: dict[str, Any], depots: Sequence[dict[str, Any]],
              deliveries: Sequence[dict[str, Any]], matrix: TravelMatrix,
              solve=None) -> str | None:
    """Which one overlay field is responsible for a variant failing the gate.

    Reverting each `set` field in turn and re-running is a search a reader can
    follow, and it is bounded only because an overlay is one level deep: with
    chained variants the field to revert could live in any ancestor, and the
    answer would be a path through files rather than a field.

    Args:
        variant: a loaded variant file -- one carrying `base` and `set`.
        depots: records carrying `id`, `lat`, `lon`.
        deliveries: the demand.
        matrix: the pinned travel matrix.
        solve: the solver, as `check` takes it.

    Returns:
        The dotted path whose reversion makes the variant pass, or None -- the
        variant passes as it stands, or no single field accounts for it and the
        overlay has to be read as a whole.
    """
    changes = variant.get("set", {})
    if not changes or passes(check(variant, depots, deliveries, matrix, solve)):
        return None
    for path in changes:
        without = dict(variant, set={k: v for k, v in changes.items()
                                     if k != path})
        if passes(check(without, depots, deliveries, matrix, solve)):
            return path
    return None


def compare(models: Sequence[dict[str, Any]], depots: Sequence[dict[str, Any]],
            deliveries: Sequence[dict[str, Any]], matrix: TravelMatrix,
            solve=None) -> list[ModelResult]:
    """Run every model over the *same* demand, in the order given.

    Identical demand is the whole point: two models measured on two rounds are
    not comparable, and the difference would read as a property of the models.
    """
    return [check(model, depots, deliveries, matrix, solve) for model in models]
