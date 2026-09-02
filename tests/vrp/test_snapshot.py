"""Immutable snapshots and replayable plans — NFR-08, CON-4, T-89.

`NFR-08`: "Input snapshot, solver configuration, and output plan are immutable
and retained for the regulatory retention period; a plan is replayable from its
snapshot."

The half that can be built here is the snapshot and the replay. Where the bytes
are kept for a retention period is §10.1's object store and is a deployment
decision; what makes retention worth anything is that the thing retained is
sufficient and tamper-evident, which is what these check.

**The first thing this found was that it was not sufficient.** `Problem.to_dict`
and `from_dict` are the model's round trip and dropped thirty fields — every
vehicle cost, hours-of-service rules, skills, site access, batteries, reloads,
locks, synchronisations, speed profiles, order incompatibilities and ride
times. Nothing noticed because the only test exercising them used a problem
with none of those set. A snapshot built on that codec would have replayed a
strictly easier problem and reported a match, which is precisely the audit
failure NFR-08 exists to prevent.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vrp.bench.fixtures import maximal_problem
from vrp.model import Problem
from vrp.snapshot import (
    SnapshotTampered,
    capture,
    read,
    replay,
    write,
)

HOUR = 3600

# Fields that cannot be set alongside the ones the maximal instance does set,
# with the reason. Anything not listed here has to be exercised.
EXCLUSIONS = {
    ("Problem", "speed_profile"):
        "mutually exclusive with speed_profiles, which is the richer form",
}


# --------------------------------------------------------------------------
# The instance the round trip is measured with
# --------------------------------------------------------------------------

def every_dataclass_in(problem):
    """Each dataclass instance reachable from a problem, with its class name."""
    seen = []

    def walk(value):
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            seen.append(value)
            for field in dataclasses.fields(value):
                walk(getattr(value, field.name))
        elif isinstance(value, (tuple, list)):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(problem)
    return seen


def test_the_maximal_problem_sets_every_field_of_every_type_it_reaches():
    """The guard that keeps the round-trip test meaningful as the model grows.

    A field added to `Vehicle` tomorrow and left at its default here would be
    carried by no test, and the round trip below would keep passing while
    quietly dropping it — which is exactly how thirty fields came to be lost.
    This fails instead, naming the field.
    """
    exercised: set[tuple[str, str]] = set()
    present: set[tuple[str, str]] = set()
    for instance in every_dataclass_in(maximal_problem()):
        name = type(instance).__name__
        for field in dataclasses.fields(instance):
            if field.default is dataclasses.MISSING:
                continue
            present.add((name, field.name))
            if getattr(instance, field.name) != field.default:
                exercised.add((name, field.name))

    # Per field, not per instance: the model has rules about which combinations
    # mean anything -- a STATUTORY order may carry no prize -- so no single
    # instance can set everything, and demanding it would only teach the
    # fixture to violate the domain.
    missing = sorted(f"{cls}.{field}" for cls, field in present - exercised
                     if (cls, field) not in EXCLUSIONS)
    assert not missing, (
        "no instance in the maximal problem sets these, so the round trip "
        "does not exercise them and dropping one would go unnoticed: "
        + ", ".join(missing))


# --------------------------------------------------------------------------
# The round trip the snapshot stands on
# --------------------------------------------------------------------------

def test_a_fully_populated_problem_survives_the_model_round_trip():
    problem = maximal_problem()
    assert Problem.from_dict(problem.to_dict()) == problem


def test_the_snapshot_payload_is_json_and_round_trips_through_it():
    """`to_dict` yields frozensets and tuples, which JSON has no spelling for.
    A snapshot that cannot be written as bytes cannot be retained."""
    problem = maximal_problem()
    snapshot = capture(problem, {"solver": "pyvrp", "seed": 7,
                                 "iterations": 500})

    payload = json.dumps(snapshot.payload)          # must not raise
    assert Problem.from_dict(json.loads(payload)["problem"]) == problem


# --------------------------------------------------------------------------
# Immutability
# --------------------------------------------------------------------------

def test_the_digest_covers_the_problem_and_the_configuration():
    problem = maximal_problem()
    base = capture(problem, {"solver": "pyvrp", "seed": 7})

    assert capture(problem, {"solver": "pyvrp", "seed": 7}).digest == base.digest
    assert capture(problem, {"solver": "pyvrp", "seed": 8}).digest != base.digest, (
        "the seed is part of what makes a plan reproducible and must be sealed")


def test_a_changed_field_anywhere_changes_the_digest():
    """Every field, not just the ones somebody remembered."""
    problem = maximal_problem()
    base = capture(problem, {"seed": 1}).digest

    changed = dataclasses.replace(
        problem, vehicles=(dataclasses.replace(problem.vehicles[0],
                                               cost_per_metre=99),))
    assert capture(changed, {"seed": 1}).digest != base, (
        "a vehicle cost changed and the digest did not, so the snapshot does "
        "not seal what the plan was optimised against")


def test_a_tampered_snapshot_is_refused_on_read(tmp_path):
    path = tmp_path / "snap.json"
    write(capture(maximal_problem(), {"seed": 1}), path)

    raw = json.loads(path.read_text())
    raw["payload"]["config"]["seed"] = 2
    path.write_text(json.dumps(raw))

    with pytest.raises(SnapshotTampered, match="digest"):
        read(path)


def test_an_untampered_snapshot_reads_back_equal(tmp_path):
    path = tmp_path / "snap.json"
    original = capture(maximal_problem(), {"solver": "pyvrp", "seed": 3})
    write(original, path)

    restored = read(path)
    assert restored.digest == original.digest
    assert restored.problem() == maximal_problem()


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------

def test_a_plan_is_re_derived_from_the_snapshot_alone(tmp_path):
    """NFR-08's sentence, tested as written: from the snapshot, not from the
    objects that happened to be in memory when it was taken."""
    problem = maximal_problem()
    snapshot = capture(problem, {"solver": "stub", "seed": 5})
    path = tmp_path / "snap.json"
    write(snapshot, path)

    def solve(instance: Problem, config: dict) -> list[str]:
        assert config["seed"] == 5
        return [order.id for order in instance.orders] + [
            str(instance.vehicles[0].cost_per_metre)]

    first = replay(read(path), solve)
    second = replay(read(path), solve)
    assert first == second == ["O1", "O2", "2"]


def test_replaying_a_lossy_snapshot_would_have_been_caught(tmp_path):
    """The failure this task existed to prevent, stated as a test.

    A snapshot that dropped the vehicle's costs would replay a problem that is
    cheaper to serve and would still call the result a match. The assertion is
    on what the replayed instance *contains*, not on whether replay ran.
    """
    snapshot = capture(maximal_problem(), {"seed": 1})
    restored = snapshot.problem()

    vehicle = restored.vehicles[0]
    assert vehicle.cost_per_metre == 2 and vehicle.hos_rules == "EU-561"
    assert vehicle.battery_wh == 60_000 and vehicle.max_reloads == 2
    assert restored.locks and restored.synchronisations
    assert restored.speed_profiles is not None
    assert restored.orders[0].incompatible_with == frozenset({"raw", "hazardous"})


def test_the_digest_is_the_same_in_a_process_with_a_different_hash_seed():
    """CON-4 asks for replayability, which means across processes and days.

    Python randomises string hashing per process, so a frozenset iterates in a
    different order in the next one. Encoding a set in iteration order gives a
    digest that is stable in any single test run and different tomorrow, which
    is the worst version: an audit trail that verifies until somebody restarts
    the service. Sorting is what makes the bytes a function of the contents.
    """
    script = (
        "import sys, json; sys.path.insert(0, '.'); sys.path.insert(0, 'tests/vrp');"
        "from test_snapshot import maximal_problem;"
        "from vrp.snapshot import capture;"
        "print(capture(maximal_problem(), {'seed': 1}).digest)"
    )
    digests = set()
    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            cwd=Path(__file__).resolve().parents[2])
        digests.add(result.stdout.strip())

    assert len(digests) == 1, (
        f"the digest depends on the hash seed: {sorted(digests)}. A snapshot "
        "written today would fail verification after a restart")
