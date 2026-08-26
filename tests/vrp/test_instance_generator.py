"""Randomised instances against INV-1…INV-9 — SDD §11.1 L2, T-05, E-04.

§11.1's L2 gate is "zero violations over 10⁵ generated cases". That number does
not fit in a per-commit suite, so this file runs two properties at different
prices:

* **Cheap, and the one that can actually reach 10⁵.** Generate an instance,
  build a plan with the canonical evaluator, and have the independent verifier
  judge it. About a millisecond each, so 10⁵ is a couple of minutes rather than
  a couple of hours. Scale it with `VRP_PROPERTY_CASES`.
* **Expensive, on a small sample.** The same, with PyVRP actually solving. A
  real solve is ~70 ms, so 10⁵ would be two hours; a sample still covers the
  adapter, which the cheap property never touches.

The distinction is worth stating rather than quietly running 200 cases and
calling it L2: the gate is a soak target, and `make property-soak` is how it is
met. A test that claims 10⁵ and runs 200 would be the sort of arithmetic this
project keeps catching elsewhere.

What makes this worth having over the hand-written fixtures in
`test_independent_verifier.py`: those fixtures encode the failures somebody
already thought of. A generator that varies capacity pressure, window
tightness, depot count and hours-of-service finds the combinations nobody did.
"""

from __future__ import annotations

import os

import pytest

from vrp.generate import Shape, build_plan, generate_instance
from vrp.model import Route, Solution
from vrp.verify import verify

# 200 keeps the suite quick. The L2 gate is 10⁵ and is met by the soak target;
# see the module docstring.
DEFAULT_CASES = 200
CASES = int(os.environ.get("VRP_PROPERTY_CASES", str(DEFAULT_CASES)))
SOLVE_CASES = int(os.environ.get("VRP_PROPERTY_SOLVE_CASES", "12"))


def _solution(problem) -> Solution:
    """Plan the instance and wrap it as a Solution the verifier can judge."""
    assignment, timelines = build_plan(problem)
    routes = [Route(vehicle_id=vehicle_id, steps=steps)
              for vehicle_id, steps in timelines.items()]
    served = {o for ids in assignment.values() for o in ids}
    unassigned = tuple(
        {"order_id": order.id, "reason_code": "NOT_PLACED",
         "explanation": "left unassigned by the greedy constructor"}
        for order in problem.orders if order.id not in served
    )
    return Solution(problem_id=problem.id, routes=tuple(routes),
                    unassigned=unassigned, objective_breakdown={},
                    status="FEASIBLE")


def test_generated_instances_are_internally_valid():
    """The generator must not emit a Problem the model itself rejects.

    Cheap, and it runs first: a generator that produces invalid instances would
    make every property below fail for a reason that has nothing to do with the
    invariant under test.
    """
    for seed in range(CASES):
        problem = generate_instance(seed)          # constructing validates
        assert problem.orders and problem.vehicles
        assert len({o.id for o in problem.orders}) == len(problem.orders)
        size = len(problem.matrix.durations)
        assert all(location.matrix_index < size for location in problem.locations)


def test_generated_instances_are_reproducible_from_their_seed():
    """§11.1 L3 leans on this, and so does every bug report about a failure
    found here: a case that cannot be regenerated cannot be investigated."""
    for seed in (0, 1, 7, 99):
        first, second = generate_instance(seed), generate_instance(seed)
        assert first.matrix.durations == second.matrix.durations
        assert [o.id for o in first.orders] == [o.id for o in second.orders]
        assert [v.capacities for v in first.vehicles] == \
               [v.capacities for v in second.vehicles]


def test_the_verifier_finds_no_violations_on_generated_plans():
    """The L2 property: INV-1…INV-9 hold over randomised instances.

    The plan comes from the canonical evaluator and the judgement from the
    independent verifier, which shares no code with it. Randomising the
    instance is what makes this more than the hand-written agreement test:
    the shapes vary across capacity pressure, window tightness, depot count
    and hours-of-service.
    """
    failures = []
    for seed in range(CASES):
        problem = generate_instance(seed)
        report = verify(problem, _solution(problem))
        if not report.ok:
            failures.append((seed, [str(v) for v in report.violations[:3]]))
    assert not failures, (
        f"{len(failures)}/{CASES} generated cases violated an invariant; "
        f"first three: {failures[:3]}")


@pytest.mark.parametrize("shape", list(Shape))
def test_every_shape_is_generated_and_verifies(shape):
    """Each shape separately, so a shape that never fires cannot hide.

    A generator whose "tight capacity" branch silently produced slack
    instances would still pass the property above, and would be measuring
    nothing while appearing to measure everything.
    """
    for seed in range(40):
        problem = generate_instance(seed, shape=shape)
        report = verify(problem, _solution(problem))
        assert report.ok, (f"{shape.name} seed {seed}: "
                           + "; ".join(str(v) for v in report.violations[:2]))


def test_tight_shapes_actually_bind():
    """The generator must produce hard instances, not merely claim to.

    Without this the suite could run ten thousand roomy instances and report a
    green L2 gate having exercised nothing. Compares the shapes against each
    other rather than against a magic number, so it stays true if the
    generator's scale changes.
    """
    def unplaced(shape: Shape) -> int:
        return sum(len(_solution(p).unassigned)
                   for p in (generate_instance(s, shape=shape) for s in range(40)))

    assert unplaced(Shape.TIGHT_CAPACITY) > unplaced(Shape.SLACK), \
        "TIGHT_CAPACITY left no more orders unplaced than SLACK did"
    assert unplaced(Shape.TIGHT_WINDOWS) > unplaced(Shape.SLACK), \
        "TIGHT_WINDOWS left no more orders unplaced than SLACK did"


def test_the_solver_also_produces_verifiable_plans_on_generated_instances():
    """The expensive property, on a sample. Covers the PyVRP adapter, which the
    evaluator-built plans above never exercise."""
    from vrp.solve.pyvrp_adapter import solve

    failures = []
    for seed in range(SOLVE_CASES):
        problem = generate_instance(seed, shape=Shape.SLACK)
        report = verify(problem, solve(problem, iterations=60, seed=seed))
        if not report.ok:
            failures.append((seed, [str(v) for v in report.violations[:2]]))
    assert not failures, f"solver produced invalid plans: {failures[:3]}"
