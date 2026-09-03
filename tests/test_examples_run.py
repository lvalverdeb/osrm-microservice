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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "src"

# The command every example's own usage line names. It matters: the examples are
# a separate workspace package, and installing it is what puts `config` on
# `sys.path` and `folium` in the environment. Running them with a bare
# interpreter worked on a laptop that had run `make examples` at some point and
# failed in CI on a clean checkout -- a gate that runs the thing differently
# from the way it is documented tests a configuration nobody has.
RUNNER = ["uv", "run", "--package", "osrm-api-gateway-examples"]

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
BROKEN: dict[str, str] = {}
"""Examples known broken, pinned rather than hidden.

Empty, and that is the point: Phase 1 of `docs/planning/VRP_EXAMPLES_PLAN.md`
cleared both entries -- `dispatch_waves` called `decide(postponed_to=...)` and
passed a two-argument policy, and `prizes_and_priority` indexed `tier_bonuses`
by a bare tier after `T-74` re-keyed it by `precedence()`. An entry here is a
strict xfail: fixing one without removing it fails this file, which is what
stops a repair going unrecorded.
"""


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

    result = subprocess.run(RUNNER + [str(path)], cwd=REPO,
                            capture_output=True, text=True, timeout=300,
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
    """The claim in a docstring is a promise to whoever reads it first.

    Five examples said "Runs offline. No gateway required" and then called
    `build_matrix(GATEWAY, ...)` with no fallback, so they died on a refused
    connection. They now go through `dataset.road_matrix_or_planar`, which
    falls back to straight-line distances and says so -- the promise made true
    rather than deleted.

    The check is that the *promise* holds, not that the call is absent: an
    example may reach for a road matrix as long as it works without one.
    """
    liars = []
    for path in ALL:
        source = path.read_text(encoding="utf-8")
        head = source[:source.find('"""', 3) + 3] if source.startswith('"""') else ""
        if "Runs offline" not in head:
            continue
        if "build_matrix(" in source and "road_matrix_or_planar" not in source:
            liars.append(name_of(path))

    assert not liars, (
        f"{sorted(liars)} promise to run offline and call build_matrix with no "
        "fallback, so they die on a refused connection")
