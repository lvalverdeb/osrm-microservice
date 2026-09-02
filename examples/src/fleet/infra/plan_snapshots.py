"""Proving a plan came from the inputs somebody says it came from.

Demonstrates immutable snapshots landed for E-89/T-89 (NFR-08, CON-4):

    vrp.snapshot   seal a problem and its configuration; replay from the seal

`NFR-08`: "Input snapshot, solver configuration, and output plan are immutable
and retained for the regulatory retention period; a plan is replayable from its
snapshot."

Retention is a storage decision (§10.1 names an object store). What a library
can own is the two properties that make retention worth anything: the record
has to be **sufficient** to re-derive the plan, and **tamper-evident** so that
what comes back is what went in.

Four things, in order:

1. **The bug this found first.** The model's own round trip dropped thirty
   fields -- every vehicle cost, hours-of-service rules, site access,
   batteries, reloads, locks, synchronisations, order incompatibilities. A
   snapshot built on it would have replayed a cheaper, more permissive problem
   and called the result a faithful replay.

2. **What a sufficient record contains.** The same problem, sealed and rebuilt
   from JSON, compares equal to the original -- field for field, including the
   collection types JSON has no spelling for.

3. **Tamper-evidence.** One digit changed in a retained file, and the reader
   refuses rather than replaying something that is no longer what was planned.

4. **Replay.** The plan comes back from the record alone, not from whatever
   objects happened to be in memory.

Runs offline.

Usage:
    uv run --package osrm-api-gateway-examples \\
        examples/src/fleet/infra/plan_snapshots.py
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from vrp.bench.fixtures import maximal_problem
from vrp.model import Problem
from vrp.snapshot import SnapshotTampered, capture, read, replay, write


def heading(number: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}  {title}\n{'=' * 72}")


def what_was_being_dropped() -> None:
    heading("1.", "What the round trip used to lose")
    problem = maximal_problem()
    vehicle = problem.vehicles[0]
    print("\n   a vehicle as the planner sees it:\n")
    for field in ("cost_per_metre", "overtime_cost_per_second", "hos_rules",
                  "access_class", "battery_wh", "max_reloads"):
        print(f"      {field:26s} {getattr(vehicle, field)!r}")
    print("\n   Until T-89 every one of those came back as its default. The")
    print("   replayed problem was cheaper to serve, legal in more ways, and")
    print("   reported as a faithful reproduction of the original.")


def a_sufficient_record() -> None:
    heading("2.", "The record, and whether it is enough")
    problem = maximal_problem()
    snapshot = capture(problem, {"solver": "pyvrp", "seed": 7,
                                 "iterations": 500,
                                 "matrix_version": problem.matrix.version})
    payload = json.dumps(snapshot.payload)
    rebuilt = Problem.from_dict(json.loads(payload)["problem"])

    print(f"\n   sealed: {len(payload):,} bytes of JSON")
    print(f"   digest: {snapshot.digest[:32]}...")
    fields = sum(len(dataclasses.fields(v)) for v in (problem, problem.vehicles[0],
                                                      problem.orders[0]))
    print(f"\n   rebuilt from those bytes alone, and equal to the original: "
          f"{rebuilt == problem}")
    print(f"   ({fields} fields across Problem, Vehicle and Order alone, and")
    print("   equality is field-for-field including the frozensets and tuples")
    print("   JSON has no spelling for.)")


def tamper_evidence() -> None:
    heading("3.", "One digit changed in a retained file")
    problem = maximal_problem()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "plan-2026-09-02.json"
        write(capture(problem, {"solver": "pyvrp", "seed": 7}), path)
        print(f"\n   written:  {path.name}")
        print(f"   verified: {read(path).digest[:32]}...")

        raw = json.loads(path.read_text())
        raw["payload"]["config"]["seed"] = 8
        path.write_text(json.dumps(raw))
        print("\n   somebody edits the seed from 7 to 8 and re-saves:\n")
        try:
            read(path)
        except SnapshotTampered as refusal:
            for line in str(refusal).split(". "):
                print(f"      {line.strip()}")
    print("\n   Not repaired, not warned about. A record that does not match")
    print("   its own digest is not a record of anything.")


def replaying() -> None:
    heading("4.", "The plan, re-derived from the record")
    problem = maximal_problem()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "snap.json"
        write(capture(problem, {"solver": "stub", "seed": 5}), path)

        def solve(instance: Problem, config: dict) -> dict:
            """Stands in for a solver: reports what it was handed."""
            return {"seed": config["seed"],
                    "orders": [order.id for order in instance.orders],
                    "cost_per_metre": instance.vehicles[0].cost_per_metre,
                    "hos": instance.vehicles[0].hos_rules}

        first = replay(read(path), solve)
        second = replay(read(path), solve)

    print(f"\n      first replay:  {first}")
    print(f"      second replay: {second}")
    print(f"\n   identical: {first == second}")
    print("\n   And the instance handed to the solver carries the costs and the")
    print("   rule set, so what is replayed is the problem that was planned")
    print("   rather than an easier one wearing its name.")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    print("\nNFR-08 and CON-4. Retention is §10.1's store; this is the record.")
    what_was_being_dropped()
    a_sufficient_record()
    tamper_evidence()
    replaying()
    print(f"\n{'=' * 72}")
    print("An audit trail that cannot re-derive the plan is a filing cabinet.")
    print("The digest is what makes it evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
