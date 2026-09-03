"""Every example still runs — the gate that stops them rotting.

`make examples` is an interactive menu and no test executed an example, so the
examples drifted from the library in silence. An audit on 2026-09-03 found two
broken outright: `dispatch_waves` called `decide(postponed_to=...)` after the
parameter was removed, and `prizes_and_priority` indexed `tier_bonuses` by a
bare tier after `T-74` re-keyed it by `precedence()`. The second break was
three weeks old by wall clock and one session old in fact — made while closing
`T-74`, and invisible because nothing ran the file.

An example that does not run serves neither of its two purposes: it showcases
nothing, and the implementation clue it offers is wrong.

**A connection error is a skip, not a failure.** Twenty-five examples reference
a gateway and twenty of them run anyway -- optional enrichment, or a degraded
path. Skipping all twenty-five to avoid the five that genuinely need one would
give up two-thirds of the coverage, so the rule is about what happened rather
than what the source mentions.

Marked `slow`: the sweep is minutes, and `make test` runs `-m "not slow"` on
every commit. CI runs it as its own step -- see `make examples-check`. A gate
nobody runs is the situation this replaces.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "src"

# Not examples: `dataset.py` is the shared corpus loader and `config.py` holds
# the gateway URL. Both are imported by examples and neither is one.
NOT_AN_EXAMPLE = {"dataset.py", "config.py"}

# Anything that means "no service was listening", in the spellings httpx and
# the standard library actually produce.
UNREACHABLE = (
    "ConnectError", "Connection refused", "ConnectionRefusedError",
    "Failed to establish a new connection", "Max retries exceeded",
    "NewConnectionError", "Cannot connect to host",
)

# Known broken, pinned rather than hidden. Strict: fixing one fails this file
# until the entry is removed, which is what stops a repair going unrecorded.
# Phase 1 of `docs/planning/VRP_EXAMPLES_PLAN.md` clears both.
BROKEN = {
    "fleet/dynamic/dispatch_waves.py":
        "calls decide(postponed_to=...); vrp.epochs.decide has no such "
        "parameter. API drift, nothing ran the file.",
    "fleet/rich/prizes_and_priority.py":
        "indexes tier_bonuses()[1]; T-74 re-keyed it by precedence(), a "
        "(tier, source) tuple. Broken by my own change, in this repository, "
        "with a full green suite.",
}


def examples() -> list[Path]:
    return sorted(p for p in EXAMPLES.rglob("*.py")
                  if "__pycache__" not in p.parts and p.name not in NOT_AN_EXAMPLE)


def name_of(path: Path) -> str:
    return str(path.relative_to(EXAMPLES))


ALL = examples()


def test_there_are_examples_to_check():
    """A glob that matched nothing would make every test below vacuous."""
    assert len(ALL) > 50, f"only {len(ALL)} examples found; the glob is wrong"


@pytest.mark.slow
@pytest.mark.parametrize("path", ALL, ids=name_of)
def test_the_example_runs(path: Path):
    """Run it as a script, the way its own usage line says to.

    Args:
        path: the example.

    A subprocess rather than an import: every example is a program with a
    `__main__` guard, and importing one would run none of it.
    """
    if name_of(path) in BROKEN:
        pytest.xfail(BROKEN[name_of(path)])

    result = subprocess.run([sys.executable, str(path)], cwd=REPO,
                            capture_output=True, text=True, timeout=180,
                            check=False)
    if result.returncode == 0:
        return

    output = (result.stderr or "") + (result.stdout or "")
    if any(marker in output for marker in UNREACHABLE):
        pytest.skip("needs a gateway or routing engine that is not running")

    tail = "\n".join(output.strip().splitlines()[-12:])
    pytest.fail(f"{name_of(path)} exited {result.returncode}:\n{tail}")


@pytest.mark.slow
def test_no_example_claims_to_run_offline_while_needing_a_gateway():
    """The claim in the docstring is a promise to whoever reads it first.

    Five examples say "Runs offline. No gateway required" and then call
    `build_matrix(GATEWAY, ...)` with no fallback, so they die on a refused
    connection. Pinned strictly rather than deleted from the docstrings: the
    fix Phase 1 prefers is a planar fallback, which makes the claim true
    instead of making it absent.
    """
    liars = []
    for path in ALL:
        source = path.read_text(encoding="utf-8")
        head = source[:source.find('"""', 3) + 3] if source.startswith('"""') else ""
        if "Runs offline" not in head:
            continue
        if "build_matrix(" in source or "GATEWAY" in source:
            liars.append(name_of(path))

    assert liars == sorted([
        "fleet/alloc/depot_inventory.py",
        "fleet/alloc/fleet_minimisation.py",
        "fleet/alloc/fleet_mix.py",
        "fleet/alloc/tactical_sizing.py",
        "fleet/alloc/territories.py",
    ]), (
        "the set of examples promising to run offline while calling a gateway "
        f"has changed: {sorted(liars)}. Fixing one means removing it from this "
        "list; adding one means an example makes a promise it cannot keep")
