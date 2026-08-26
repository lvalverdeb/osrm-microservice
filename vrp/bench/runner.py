"""Benchmark runner and the regression gate — SDD §11.3, CON-9, T-16.

Runs the frozen corpus at a declared budget, verifies every plan, and compares
the result against `benchmarks/baseline.json`.

Two rules the gate enforces, in this order:

1. **An unverified plan fails, whatever it cost.** CON-1 puts feasibility above
   optimality, so a cheap illegal plan is not an improvement — it is a defect
   that happens to score well. Checking cost first would let one through.
2. **Then quality**, on §11.3's thresholds: the mean must not worsen by more
   than 0.25 percentage points, and no single instance may regress by more
   than 2%.

Budget is iterations rather than seconds, deliberately. §11.3 asks for a
declared time budget on declared hardware, which is right for reporting absolute
quality against published BKS. It is wrong for a CI gate: wall-clock varies with
the runner, so a time budget makes the gate fail on a busy machine instead of on
a real regression. Iterations are reproducible.

Placement: Python. This drives the solver and is not on any request path.
"""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from vrp.bench.corpus import CORPUS, Spec, build_instance
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

BASELINE_JSON = Path("benchmarks/baseline.json")
BASELINE_MD = Path("benchmarks/BASELINE.md")

# §11.3 gate policy.
MEAN_TOLERANCE_PP = 0.25
INSTANCE_TOLERANCE_PCT = 2.0


@dataclass(frozen=True)
class Result:
    name: str
    cost: int
    distance: int
    vehicles: int
    verified: bool
    violations: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    report: str


def run_instance(spec: Spec, iterations: int, seed: int = 0) -> Result:
    """Solve one instance and have the independent verifier judge the plan."""
    problem = build_instance(spec)
    solution = solve(problem, iterations=iterations, seed=seed)
    report = verify(problem, solution)

    # Cost is recomputed here from the matrix rather than taken from the
    # solver, for the same reason INV-9 exists: a solver's own accounting is
    # not evidence about the solver.
    matrix = problem.matrix
    distance = 0
    for route in solution.routes:
        for previous, current in zip(route.steps, route.steps[1:], strict=False):
            distance += matrix.distance(
                problem.location(previous.location_id).matrix_index,
                problem.location(current.location_id).matrix_index)
    vehicles = sum(1 for route in solution.routes
                   if any(step.order_id for step in route.steps))

    return Result(name=spec.name, cost=distance, distance=distance,
                  vehicles=vehicles, verified=report.ok,
                  violations=tuple(str(v) for v in report.violations))


def run_corpus(iterations: int = 500, seed: int = 0) -> list[Result]:
    return [run_instance(spec, iterations=iterations, seed=seed) for spec in CORPUS]


def gate(results: list[Result], recorded: dict) -> Verdict:
    """Compare a run against the recorded baseline. §11.3 gate policy."""
    baseline = {entry["name"]: entry for entry in recorded["instances"]}
    lines: list[str] = []

    # Rule 1: feasibility first. A plan that does not verify is not a datapoint.
    unverified = [r for r in results if not r.verified]
    if unverified:
        for result in unverified:
            lines.append(f"  {result.name}: FAILED VERIFICATION "
                         f"-- {'; '.join(result.violations[:2])}")
        return Verdict(False, "plans failed verification, so cost is not "
                              "comparable:\n" + "\n".join(lines))

    # Rule 2: quality against the recorded numbers.
    deltas: list[float] = []
    worst: list[str] = []
    for result in results:
        previous = baseline.get(result.name)
        if previous is None:
            return Verdict(False, f"{result.name} is not in the baseline; "
                                  f"re-record after changing the corpus")
        delta_pct = (result.cost - previous["cost"]) / previous["cost"] * 100
        deltas.append(delta_pct)
        marker = ("unchanged" if delta_pct == 0
                  else "regressed" if delta_pct > 0 else "improved")
        lines.append(f"  {result.name:<26} {previous['cost']:>8} -> "
                     f"{result.cost:>8}  {delta_pct:+.2f}%  {marker}")
        if delta_pct > INSTANCE_TOLERANCE_PCT:
            worst.append(f"{result.name} regressed {delta_pct:+.2f}% "
                         f"(limit {INSTANCE_TOLERANCE_PCT}%)")

    mean = sum(deltas) / len(deltas)
    summary = f"\nmean {mean:+.2f}pp (limit {MEAN_TOLERANCE_PP}pp)"
    body = "\n".join(lines) + summary

    if worst:
        return Verdict(False, "instance regressed past the limit:\n"
                       + "\n".join(f"  {w}" for w in worst) + "\n" + body)
    if mean > MEAN_TOLERANCE_PP:
        return Verdict(False, f"mean regressed {mean:+.2f}pp, past "
                              f"{MEAN_TOLERANCE_PP}pp\n" + body)
    return Verdict(True, body)


def record(iterations: int, seed: int = 0) -> dict:
    """Run the corpus and write the baseline. Overwrites deliberately."""
    results = run_corpus(iterations=iterations, seed=seed)
    unverified = [r for r in results if not r.verified]
    if unverified:
        raise SystemExit(
            "refusing to record a baseline from plans that do not verify: "
            + ", ".join(r.name for r in unverified))

    payload = {
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "solver": "pyvrp",
        "iterations": iterations,
        "seed": seed,
        # Hardware is recorded because §11.3 requires it for absolute-quality
        # reporting. The gate itself does not compare across machines -- that is
        # what the iteration budget is for.
        "hardware": f"{platform.system()} {platform.machine()}",
        "instances": [
            {k: v for k, v in asdict(r).items() if k != "violations"}
            for r in results
        ],
    }
    BASELINE_JSON.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    _write_markdown(payload)
    return payload


def _write_markdown(payload: dict) -> None:
    total = sum(entry["cost"] for entry in payload["instances"])
    rows = "\n".join(
        f"| `{e['name']}` | {e['cost']:,} m | {e['vehicles']} |"
        for e in payload["instances"]
    )
    BASELINE_MD.write_text(f"""# Benchmark baseline

Recorded by `python -m vrp.bench.runner --record`. **Do not edit by hand** --
these numbers are only meaningful as the output of a run, and a hand-edited
baseline silently redefines what every later comparison means.

| Field | Value |
|---|---|
| Recorded | {payload['recorded_at']} |
| Solver | {payload['solver']} |
| Budget | {payload['iterations']} iterations, seed {payload['seed']} |
| Hardware | {payload['hardware']} |

| Instance | Total distance | Vehicles |
|---|---|---|
{rows}
| **Total** | **{total:,} m** | |

## What these numbers are, and are not

They are a **regression baseline**: the gate in `vrp/bench/runner.py` fails a
change that makes the mean worse by more than {MEAN_TOLERANCE_PP} percentage
points, or any single instance worse by more than {INSTANCE_TOLERANCE_PCT}%.

They are **not** a quality claim. Gap against published best-known solutions
needs the public instance readers and the BKS registry (`T-06`), and until those
land there is nothing here to compare against but ourselves. SDD §11.3 is
explicit that its initial targets are targets rather than claims; this file does
not upgrade them.

The budget is **iterations, not seconds**. Wall-clock varies with the machine,
which would make the gate fail on a busy CI runner rather than on a real
regression. Absolute-quality reporting against published BKS does need a time
budget on declared hardware, and will get one when it has something to report.

## Re-recording

Changing `vrp/bench/corpus.py` changes what every number above means, so the
corpus and this file move together:

```sh
python -m vrp.bench.runner --record --iterations {payload['iterations']}
```

Commit both, and say in the commit why the corpus changed.
""")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true",
                        help="run the corpus and overwrite the baseline")
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    if args.record:
        payload = record(iterations=args.iterations, seed=args.seed)
        print(f"recorded {len(payload['instances'])} instances at "
              f"{payload['iterations']} iterations -> {BASELINE_JSON}")
        return 0

    if not BASELINE_JSON.exists():
        print("no baseline; run with --record first")
        return 2
    recorded = json.loads(BASELINE_JSON.read_text())
    verdict = gate(run_corpus(iterations=recorded["iterations"],
                              seed=recorded["seed"]), recorded)
    print(verdict.report)
    print("\nGATE:", "pass" if verdict.ok else "FAIL")
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
