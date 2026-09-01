"""What the engine scores on problems other people have already solved.

Demonstrates the public benchmark anchors of `CAT-VRP-003` §13.3 — "each
variant section contributes at least one benchmark-comparable fixture so public
benchmark performance and production performance can be related":

    vrp.benchmarks       VRPLIB, Solomon and TSPLIB into our own domain model
    vrp.solve            the same solver that plans a Costa Rica round
    vrp.verify           the same independent verifier, judging both
    vrp.bench.catalogue  which catalogue scenarios each anchor stands for

The point of a public instance is not that it is hard. It is that somebody else
has already solved it, so a number produced here can be wrong in a way a
synthetic instance can never reveal. §11.3: a benchmark number without its
budget and hardware is meaningless, so both are printed.

Three things this shows and one it refuses to:

1. **Every shipped instance, read into one model.** A Solomon VRPTW, a CVRPLIB
   CVRP, a Li & Lim PDPTW, a TSPLIB tour and a two-depot fixture all become the
   same `Problem`, and the same verifier judges every plan.

2. **The gap, where the file states an optimum.** `E-n22-k4` carries "Optimal
   value: 375" in its own COMMENT and `RC208` has a sibling `.sol`. Those are
   read. Where a file says nothing, the gap column says nothing -- a
   hand-typed registry of best-known values is a registry of typos, and every
   gap computed against a wrong one is wrong while looking fine.

3. **Which catalogue variants are thereby evidenced.** §5's sixteen TSP
   scenarios and the multi-depot half of §8's twenty-nine had no anchor until
   `pr107.tsp` and `OkSmallMultipleDepots.txt` arrived.

And the refusal: `PR01.vrp` is site-dependent, and the mapping raises rather
than dropping the section. An instance that parses cleanly into a *different*
problem is worse than one that will not parse at all, because nothing looks
wrong afterwards.

Runs offline. The instances are vendored in `benchmarks/instances/`.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/benchmarking/published_instances.py --iterations 5000
"""

from __future__ import annotations

import argparse
import sys
import time
from itertools import pairwise
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.bench.catalogue import by_variant, load, operational
from vrp.benchmarks import gap_percent, read_benchmark
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

INSTANCES = PROJECT_ROOT / "benchmarks" / "instances"

# Which catalogue section each anchor stands for. The variant names are the
# catalogue's own closed vocabulary (§0.3), so these resolve against the
# extract rather than being a second opinion about what the sets cover.
ANCHORS: dict[str, str] = {
    "pr107.tsp": "TSP",
    "E-n22-k4.txt": "CVRP",
    "X-n101-50-k13.vrp": "CVRP",
    "RC208.vrp": "VRPTW",
    "lrc206.vrp": "PDPTW",
    "OkSmallMultipleDepots.txt": "MDHVRPTW",
    "SmallVRPSPD.vrp": "CVRP",
}


def tour_distance(problem, solution) -> int:
    return sum(
        problem.matrix.distance(problem.location(a.location_id).matrix_index,
                                problem.location(b.location_id).matrix_index)
        for route in solution.routes for a, b in pairwise(route.steps))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iterations", type=int, default=5_000,
                        help="search budget per instance (default: 5000)")
    args = parser.parse_args()

    print("What the engine scores on problems other people have already solved.")
    print(f"\nCAT-VRP-003 §13.3 -- public anchors, {args.iterations} iterations "
          "each, single-threaded.")
    print("§11.3: a benchmark number without its budget is meaningless, which "
          "is why\nthe budget is in that line rather than in a footnote.\n")

    scenarios = operational(load())
    header = (f"{'instance':28s} {'variant':10s} {'ours':>9s} "
              f"{'published':>10s} {'gap':>7s}  {'verified':8s} {'seconds':>7s}")
    print(header)
    print("-" * len(header))

    refused: list[tuple[str, str]] = []
    for path in sorted(INSTANCES.glob("*")):
        if path.suffix not in (".vrp", ".txt", ".tsp"):
            continue
        try:
            benchmark = read_benchmark(path)
        except NotImplementedError as refusal:
            refused.append((path.name, str(refusal)))
            continue

        started = time.perf_counter()
        solution = solve(benchmark.problem, iterations=args.iterations, seed=0)
        elapsed = time.perf_counter() - started
        ours = tour_distance(benchmark.problem, solution)
        report = verify(benchmark.problem, solution)

        published = (f"{benchmark.best_known:,}" if benchmark.best_known
                     else "not stated")
        gap = (f"{gap_percent(ours, benchmark.best_known):+.2f}%"
               if benchmark.best_known else "—")
        print(f"{path.name:28s} {ANCHORS.get(path.name, '?'):10s} {ours:>9,} "
              f"{published:>10s} {gap:>7s}  {report.ok!s:8s} {elapsed:>7.1f}")

    print("\nWhere `published` says \"not stated\", the file does not carry an "
          "optimum and\nnothing here invents one. `vrp.benchmarks` reads the "
          "COMMENT line or a sibling\n`.sol`, and reports None otherwise.")

    print("\nWhat each anchor evidences")
    print("-" * 26)
    for variant in ("TSP", "CVRP", "VRPTW", "MDHVRPTW", "PDPTW"):
        count = len(by_variant(scenarios, variant))
        files = sorted(name for name, v in ANCHORS.items() if v == variant)
        print(f"  {variant:10s} {count:3d} catalogue scenarios  <- "
              f"{', '.join(files)}")
    print("\n  §5 (TSP) and the multi-depot half of §8 had no anchor at all "
          "until pr107\n  and OkSmallMultipleDepots arrived. Forty-five "
          "scenarios were being measured\n  against nothing.")

    if refused:
        print("\nRefused, by name")
        print("-" * 16)
        for name, reason in refused:
            print(f"  {name}")
            for line in _wrap(reason, 70):
                print(f"      {line}")
        print("\n  Refusing is the correct outcome, not a gap. The failure mode "
              "this guards\n  against is the third case: the section is "
              "dropped, the file maps cleanly,\n  and the engine answers a "
              "different problem while everything looks fine.")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
