"""`vrp` as something another repository can depend on — `T-102`.

64 modules that import only because `pytest` puts the repo root on
`pythonpath`. There is no `[build-system]`, no wheel, and `uv.lock` records the
root as `virtual`, so nothing outside this checkout can depend on the platform
at a version -- which is the whole of what a downstream consumer needs.

**The repository's own models are not in the wheel, and that is deliberate.**
`models/signed-envelopes.json` and `models/mixed-parcels.json` describe *this*
repository's operations; a deployment describes its own and supplies them
through `T-101`'s `VRP_MODEL_PATH`. An installed copy therefore reports no
shipped models rather than pretending ours are anybody's, and `shipped()`
returns an empty list instead of raising -- checked below, because "degrades
quietly" is a claim and not an assumption.

The static test is the one that keeps paying. A wheel that builds today proves
today; a check that every module-level third-party import is declared fails the
moment somebody adds one, which is the failure a consumer would otherwise meet
as an `ImportError` in production.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import sysconfig
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"

STDLIB = set(sys.stdlib_module_names) | set(sysconfig.get_config_vars().keys())


def declared() -> set[str]:
    """Every distribution the project declares, core or extra."""
    raw = tomllib.loads(PYPROJECT.read_text())["project"]
    names = list(raw.get("dependencies", []))
    for extra in raw.get("optional-dependencies", {}).values():
        names.extend(extra)
    return {
        name.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip()
        .replace("-", "_").lower()
        for name in names
    }


def module_level_imports() -> dict[str, set[str]]:
    """Third-party modules each `vrp` file imports at import time."""
    found: dict[str, set[str]] = {}
    for path in sorted(REPO.joinpath("vrp").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:               # module level only, not inside defs
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name not in STDLIB and name != "vrp":
                    found.setdefault(name, set()).add(
                        str(path.relative_to(REPO)))
    return found


def test_every_module_level_import_is_declared():
    """A wheel that installs and then raises ImportError is worse than one that
    refuses to build."""
    undeclared = {name: sorted(files)
                  for name, files in module_level_imports().items()
                  if name.replace("-", "_").lower() not in declared()}

    assert not undeclared, (
        "imported at module level by vrp/ and declared by no dependency or "
        f"extra: {undeclared}")


def test_the_project_declares_how_it_is_built():
    """Without this there is no wheel, and `uv.lock` keeps calling the root
    `virtual` -- which is the state this task exists to leave."""
    raw = tomllib.loads(PYPROJECT.read_text())

    assert "build-system" in raw, "no [build-system]: nothing can build a wheel"
    assert raw["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["vrp"]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out)],
                   cwd=REPO, check=True, capture_output=True)
    built = list(out.glob("*.whl"))
    assert len(built) == 1, f"expected one wheel, got {built}"
    return built[0]


def test_the_wheel_carries_the_platform_and_not_the_workshop(wheel):
    """`tests`, `parity` and `loadtest` exercise the platform; they are not it.
    Shipping them would make a consumer's install depend on our test tooling."""
    names = zipfile.ZipFile(wheel).namelist()
    tops = {n.split("/")[0] for n in names if "/" in n}

    assert "vrp" in tops
    for package in ("vrp/solve", "vrp/verify", "vrp/hos"):
        assert any(n.startswith(package + "/") for n in names), f"{package} missing"
    for excluded in ("tests", "parity", "loadtest", "examples", "gateway"):
        assert excluded not in tops, f"{excluded}/ should not ship in the wheel"

    # `vrp/bench/` reads this repository's catalogue and instance corpus from
    # paths relative to the checkout, so it cannot work installed; the reader
    # it sits beside takes its path from the caller, so that one travels.
    assert not any(n.startswith("vrp/bench/") for n in names), (
        "vrp/bench ships but resolves docs/ and benchmarks/ against a repo "
        "root an installed copy does not have")
    assert "vrp/benchmarks.py" in names


def test_an_installed_copy_has_no_models_and_says_so_quietly(wheel):
    """`model_path` falls back to a directory the wheel does not carry. A glob
    over a missing directory is empty rather than an error, so `shipped()`
    answers honestly instead of blowing up on import."""
    from vrp import servicemodel

    assert servicemodel.MODELS.name == "models"
    assert isinstance(servicemodel.shipped(), list)
