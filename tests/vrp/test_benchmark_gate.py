"""E-16 (T-16) [GATE] — benchmark harness and the regression gate. SDD §11.3, CON-9.

CON-9 is "benchmarks before opinions". This is the machinery that makes a
quality claim arguable: a frozen corpus, a declared budget, recorded baselines,
and a gate that fails when a change makes the solver worse.

**Scope.** The corpus here is frozen and generated, which §11.3 lists as a set
in its own right. Gap against *published* best-known solutions needs the public
instance readers and the BKS registry, which are `T-06`/`E-05`; until those land
the gate measures regression against our own recorded baseline rather than
absolute quality. That distinction is stated in `benchmarks/BASELINE.md` too,
because a benchmark number whose meaning is unclear is worse than none.

**Budget is iterations, not seconds.** §11.3 asks for a declared time budget on
declared hardware, which is right for reporting absolute quality. It is wrong
for a CI gate: wall-clock varies with the runner and would make the gate flaky,
failing on a busy machine rather than on a real regression. Iterations are
reproducible, so the gate compares like with like.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pyvrp", reason="solver extra not installed")

from vrp.bench.corpus import CORPUS, build_instance
from vrp.bench.runner import Result, gate, run_corpus

BASELINE = Path("benchmarks/baseline.json")


def test_the_corpus_is_frozen():
    """Same specification, same instance — or every later number is noise."""
    first = build_instance(CORPUS[0])
    second = build_instance(CORPUS[0])
    assert first.matrix.durations == second.matrix.durations
    assert [o.id for o in first.orders] == [o.id for o in second.orders]


def test_every_corpus_instance_is_feasible_by_construction():
    """Demand must fit the fleet, or the instance measures nothing.

    The first `c50-clustered-pressure` asked 274 units of a 240-unit fleet.
    PyVRP returned a best-effort plan that broke capacity, the verifier caught
    it, and the recorder refused to write a baseline. This catches it one step
    earlier, where the mistake actually is.
    """
    for spec in CORPUS:
        problem = build_instance(spec)
        demand = sum(o.quantities["units"] for o in problem.orders)
        fleet = sum(v.capacities["units"] for v in problem.vehicles)
        assert demand <= fleet, (
            f"{spec.name}: demand {demand} exceeds fleet capacity {fleet}")


def test_every_corpus_instance_solves_and_verifies():
    """A baseline built from plans nobody checked would be worthless."""
    results = run_corpus(iterations=150)
    assert len(results) == len(CORPUS)
    for result in results:
        assert result.verified, f"{result.name}: {result.violations}"


def test_the_baseline_is_committed_and_covers_the_corpus():
    assert BASELINE.exists(), "run `python -m vrp.bench.runner --record`"
    recorded = json.loads(BASELINE.read_text())
    assert {entry["name"] for entry in recorded["instances"]} == {c.name for c in CORPUS}
    # The budget the numbers were produced at travels with them; comparing
    # results from different budgets is the easiest way to fool yourself.
    assert recorded["iterations"] > 0
    assert recorded["solver"]


def test_the_solver_still_matches_its_recorded_baseline():
    """The gate itself. This is what fails when a change makes routing worse."""
    recorded = json.loads(BASELINE.read_text())
    results = run_corpus(iterations=recorded["iterations"])
    verdict = gate(results, recorded)
    assert verdict.ok, verdict.report


def test_a_worse_solution_is_caught():
    """Perturb the results, not the gate: a gate that passes everything is not one."""
    recorded = json.loads(BASELINE.read_text())
    inflated = [
        Result(name=entry["name"], cost=int(entry["cost"] * 1.05),
               distance=entry["distance"], vehicles=entry["vehicles"],
               verified=True, violations=())
        for entry in recorded["instances"]
    ]
    verdict = gate(inflated, recorded)
    assert not verdict.ok
    assert "regressed" in verdict.report.lower()


def test_an_improvement_passes_and_is_reported():
    """Getting better must never fail the gate, but should be visible."""
    recorded = json.loads(BASELINE.read_text())
    improved = [
        Result(name=entry["name"], cost=int(entry["cost"] * 0.97),
               distance=entry["distance"], vehicles=entry["vehicles"],
               verified=True, violations=())
        for entry in recorded["instances"]
    ]
    verdict = gate(improved, recorded)
    assert verdict.ok
    assert "improved" in verdict.report.lower()


def test_an_unverified_plan_fails_the_gate_regardless_of_cost():
    """A cheap illegal plan is not an improvement. CON-1 outranks quality."""
    recorded = json.loads(BASELINE.read_text())
    cheating = [
        Result(name=entry["name"], cost=1, distance=1, vehicles=1,
               verified=False, violations=("INV-5 load exceeds capacity",))
        for entry in recorded["instances"]
    ]
    verdict = gate(cheating, recorded)
    assert not verdict.ok
    assert "verif" in verdict.report.lower()
