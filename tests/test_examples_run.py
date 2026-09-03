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

import ast
import re
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

# Signs that a prerequisite is absent rather than that the example is broken.
# Three kinds, each learned from a CI run rather than guessed:
#
#   * no service listening -- the spellings httpx and the standard library
#     actually produce;
#   * an example that *handles* the missing gateway and exits non-zero with a
#     sentence about it, which is better behaviour than a traceback and must not
#     read as a failure;
#   * no delivery corpus. `data/deliveries_cr.json` is 12 MB, generated, and
#     not committed; generating it snaps fifty thousand points through OSRM, so
#     CI cannot make one either.
#
# The `.env` that points at a gateway is itself gitignored, which is why a
# laptop with a reachable jail and a clean CI checkout disagree about which of
# these examples can run at all.
MISSING_PREREQUISITE = (
    "ConnectError", "Connection refused", "ConnectionRefusedError",
    "Failed to establish a new connection", "Max retries exceeded",
    "NewConnectionError", "Cannot connect to host",
    "not reachable", "Gateway not reachable",
    "no dataset at", "deliveries_cr.json",
)

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
    if any(marker in output for marker in MISSING_PREREQUISITE):
        pytest.skip("a prerequisite is absent: a gateway, or the generated\n        delivery corpus. Neither is a fault in the example")

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


@pytest.mark.slow
def test_every_example_is_indexed_somewhere():
    """An example nobody can find showcases nothing.

    Two indexes, because the examples are two audiences. VRP capability
    examples trace to a requirement and live in `VRP_TDD_EXAMPLES.md` by
    `E-nn`; gateway examples, benchmarks and tools are listed in
    `examples/README.md`, which also states what an example is for.

    Thirty-one files were in neither when this was written. Eleven of those
    turned out to have an `E-nn` row already, pointing at a *test* rather than
    at the example somebody had since written -- the index was describing an
    older state of the repository and nothing said so.
    """
    catalogue = (REPO / "docs" / "planning" / "VRP_TDD_EXAMPLES.md").read_text()
    readme = (REPO / "examples" / "README.md").read_text()

    missing = [name_of(p) for p in ALL
               if str(p.relative_to(REPO)) not in catalogue
               and name_of(p) not in readme]

    assert not missing, (
        "these examples appear in no index, so nobody can find them and "
        "nothing says what they are for: " + ", ".join(sorted(missing)))


# --------------------------------------------------------------------------
# The launcher, which is how anybody finds an example in the first place
# --------------------------------------------------------------------------

MENU_WIDTH = 68


def expected_title(path: Path) -> str:
    """What the menu should call this example.

    The rule, restated here rather than imported: `examples/main.py` cannot be
    imported by the test suite. It reaches for `config`, which exists only when
    the examples workspace package is installed, and `make test` runs plain
    pytest. So the menu is driven as a subprocess -- the way a person drives it
    -- and this is the rule its output is checked against.
    """
    try:
        first = (ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
                 or "").strip().split("\n")[0].strip()
    except (OSError, SyntaxError):
        first = ""
    if not first:
        return path.stem.replace("_", " ").title()
    return first if len(first) <= MENU_WIDTH else first[:MENU_WIDTH - 1] + "…"


@pytest.fixture(scope="module")
def menu() -> list[str]:
    """The launcher's own output, with stdin closed.

    EOF on the prompt is a supported exit -- `main` catches it and returns
    zero -- so this drives the real program rather than a fragment of it.
    """
    result = subprocess.run(RUNNER + [str(REPO / "examples" / "main.py")],
                            cwd=REPO, capture_output=True, text=True,
                            timeout=120, stdin=subprocess.DEVNULL, check=False)
    assert result.returncode == 0, result.stderr[-800:]
    return result.stdout.splitlines()


def listed(menu: list[str]) -> list[str]:
    return [m.group(1) for m in
            (re.match(r"^\s+\d+\.\s(.*)$", line) for line in menu) if m]


@pytest.mark.slow
def test_the_menu_lists_every_example(menu):
    """The regression this exists for: `discover_examples` walked one level
    and hid thirty-one of fifty-one examples, every rich-VRP, allocation and
    dynamic-dispatch one among them. An example nobody can find from the menu
    is documentation, not a demo."""
    assert len(listed(menu)) == len(ALL), (
        f"the menu lists {len(listed(menu))} of {len(ALL)} examples")


@pytest.mark.slow
def test_the_menu_titles_come_from_the_examples_not_the_filenames(menu):
    """Title-casing the filename gave "Ev Recharging" and "Tw Multiple
    Windows". Every example states what it shows in its first docstring line,
    and that is what a person choosing one should read."""
    assert sorted(listed(menu)) == sorted(expected_title(p) for p in ALL)


@pytest.mark.slow
def test_a_long_title_is_truncated_rather_than_wrapped(menu):
    """A menu line has to stay inside a terminal or the numbering is lost in
    the wrap."""
    assert any(title.endswith("…") for title in listed(menu)), (
        "nothing was truncated, so this instance cannot show the width rule "
        "doing anything")
    assert all(len(line) <= 80 for line in menu), (
        "a menu line runs past eighty columns: "
        + next(line for line in menu if len(line) > 80))


@pytest.mark.slow
def test_the_shared_machinery_is_not_offered_as_an_example(menu):
    """`config.py` and `dataset.py` sit at the top of `src/` and running either
    does nothing. They are excluded on purpose rather than by the accident of
    a one-level walk."""
    titles = listed(menu)
    for name in ("config.py", "dataset.py"):
        machinery = EXAMPLES / name
        # By the title the menu *would* give it, not by its filename: both
        # carry docstrings, so `"Config" not in titles` was true whether or not
        # the exclusion worked -- which the perturbation showed.
        assert expected_title(machinery) not in titles, (
            f"{name} is offered as an example; running it does nothing")
