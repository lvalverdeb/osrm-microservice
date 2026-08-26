"""Determinism and replay — CON-4, AC-1.4, T-17, E-17.

CON-4: "Given the same input snapshot, the same solver version, the same random
seed, and the same time budget expressed in *deterministic units* (iterations or
evaluations, not wall-clock), the system MUST produce byte-identical output."

This is load-bearing for claims made elsewhere. Every benchmark gap, every
regression gate threshold, and E-16's baseline all assume that re-running a
solve gives the same answer. Nothing verified it until now, so those claims
rested on an assumption rather than a check.

Four things, of which the second is the one that makes the first mean anything:

1. **Repeats are identical.** A hundred of them, compared as serialised bytes
   rather than by walking objects, because that is what "byte-identical" says.
2. **The seed actually matters.** A solver ignoring its seed passes (1)
   perfectly. This project has been caught by that shape repeatedly -- a test
   satisfied by an implementation that does nothing -- so the seed is varied and
   the output must change.
3. **No floats leak into the plan.** CON-4 prohibits floating-point
   accumulation in objective functions outright, and a float that reaches a
   serialised plan is the mechanism by which two machines disagree in the last
   bit.
4. **A run records what it would take to replay it.** CON-4 requires the
   iteration count actually achieved to be recorded, which nothing did before
   E-17 -- so a wall-clock-limited production run could not be reproduced even
   in principle.

"Across two machines" cannot be tested from one machine. What *can* be tested is
the mechanism by which Python runs differ across machines: hash randomisation
changing set and dict iteration order. `test_the_plan_does_not_depend_on_hash_
ordering` runs a subprocess with a different `PYTHONHASHSEED` and compares.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from vrp.generate import Shape, generate_instance
from vrp.solve.pyvrp_adapter import solve

REPEATS = int(os.environ.get("VRP_DETERMINISM_REPEATS", "100"))


def canonical(solution) -> str:
    """The plan as bytes. Sorted keys so dict order cannot mask a difference.

    The `solver` block is excluded deliberately. It echoes the seed and the
    budget, so including it would make `test_the_seed_actually_changes_the_
    answer` pass on the echo alone -- the plans could be identical and the test
    would never notice. The record is checked on its own terms below.
    """
    body = {k: v for k, v in asdict(solution).items() if k != "solver"}
    return json.dumps(body, sort_keys=True, default=str)


def test_a_hundred_repeats_are_byte_identical():
    """CON-4's headline, at the count T-17 names."""
    problem = generate_instance(11, shape=Shape.TIGHT_WINDOWS)
    first = canonical(solve(problem, iterations=200, seed=42))

    for attempt in range(REPEATS - 1):
        again = canonical(solve(problem, iterations=200, seed=42))
        assert again == first, f"run {attempt + 2} of {REPEATS} differed"


def test_the_seed_actually_changes_the_answer():
    """Without this, a solver that ignored its seed would pass the test above
    flawlessly -- and the guarantee would be "we always return the same thing",
    which is not determinism, it is a constant.

    Several seeds, because two seeds can coincide on a small instance without
    anything being wrong.
    """
    problem = generate_instance(5, shape=Shape.SLACK)
    plans = {canonical(solve(problem, iterations=300, seed=seed))
             for seed in range(8)}

    assert len(plans) > 1, "every seed produced the same plan; the seed is inert"


def test_the_same_seed_on_a_different_instance_is_a_different_plan():
    """The other half: a plan that is identical across *instances* would mean
    the input is being ignored, which the test above cannot detect."""
    one = canonical(solve(generate_instance(1, shape=Shape.SLACK),
                          iterations=200, seed=7))
    two = canonical(solve(generate_instance(2, shape=Shape.SLACK),
                          iterations=200, seed=7))
    assert one != two


def test_the_iteration_budget_changes_the_answer_or_the_budget_is_ignored():
    """CON-4 measures the budget in deterministic units. If iterations made no
    difference, "the same iteration budget" would be a guarantee about nothing.
    """
    # An instance the budget can actually change. A seven-order problem is
    # solved outright in one iteration, so the first fixture here showed no
    # difference between 1 and 2,000 -- which said nothing about the budget and
    # everything about the instance being trivial.
    problem = generate_instance(12, shape=Shape.SLACK)
    short = canonical(solve(problem, iterations=1, seed=0))
    long = canonical(solve(problem, iterations=2_000, seed=0))
    assert short != long, "the iteration budget had no effect on the plan"


def test_no_floats_reach_the_plan():
    """CON-4: "All internal cost/time/distance arithmetic MUST use integers in
    fixed units... Floating-point accumulation in objective functions is
    prohibited."

    A float in a serialised plan is precisely how two machines disagree in the
    last bit while both being "right", so this walks the whole structure rather
    than spot-checking the fields somebody remembered.
    """
    solution = solve(generate_instance(9, shape=Shape.MULTI_DEPOT),
                     iterations=200, seed=3)

    floats: list[str] = []

    def walk(node, path="solution"):
        if isinstance(node, float):
            floats.append(f"{path}={node!r}")
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(asdict(solution))
    assert not floats, f"floating-point values reached the plan: {floats[:5]}"


def test_a_run_records_what_it_would_take_to_replay_it():
    """CON-4: wall-clock runs are permitted "but MUST record the deterministic
    iteration count actually achieved so that any run can be replayed".

    Nothing recorded it before E-17, so a production run could not be
    reproduced even in principle -- the seed and the budget existed only as
    arguments somebody happened to pass.
    """
    problem = generate_instance(4, shape=Shape.SLACK)
    solution = solve(problem, iterations=250, seed=17)

    assert solution.solver is not None
    assert solution.solver["seed"] == 17
    assert solution.solver["iterations"] == 250
    assert solution.solver["solver"].startswith("pyvrp")
    assert solution.solver["matrix_version"] == problem.matrix.version


def test_a_recorded_run_replays_to_the_same_plan():
    """The record is only worth keeping if it is sufficient. This replays a
    solve using nothing but what the first one wrote down."""
    problem = generate_instance(6, shape=Shape.TIGHT_WINDOWS)
    original = solve(problem, iterations=180, seed=99)

    record = original.solver
    replayed = solve(problem, iterations=record["iterations"],
                     seed=record["seed"])
    assert canonical(replayed) == canonical(original)


def test_the_plan_does_not_depend_on_hash_ordering():
    """The mechanism by which two machines actually differ.

    Python randomises string hashing per process unless PYTHONHASHSEED is
    pinned, so any code iterating a set or an unordered dict can produce a
    different order per run -- and on a different machine, per machine. Two
    subprocesses with deliberately different hash seeds must agree.

    This is the closest a single machine can get to T-17's "across 2 machines",
    and it targets the real cause rather than simulating the symptom.
    """
    script = (
        "import json;from dataclasses import asdict;"
        "from vrp.generate import Shape, generate_instance;"
        "from vrp.solve.pyvrp_adapter import solve;"
        "s=solve(generate_instance(8, shape=Shape.MULTI_DEPOT),"
        " iterations=200, seed=5);"
        "print(json.dumps(asdict(s), sort_keys=True, default=str))"
    )
    outputs = []
    for hash_seed in ("0", "1", "12345"):
        environment = {**os.environ, "PYTHONHASHSEED": hash_seed}
        result = subprocess.run(
            [sys.executable, "-c", script], check=True, capture_output=True,
            text=True, env=environment, cwd=Path(__file__).resolve().parents[2])
        outputs.append(result.stdout.strip())

    assert len(set(outputs)) == 1, (
        "the plan changed with PYTHONHASHSEED, so something iterates a set or "
        "an unordered dict and will differ across machines")
