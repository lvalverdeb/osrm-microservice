"""The documents and the code, checked against each other.

Every other test in this repository asks whether the system behaves. This one
asks whether it is described accurately, which is a different failure and one
that reading does not catch: a stale marker is invisible precisely because it
reads like the rest of the document.

It was written after two audits in a row found something no review had.
`UC-019`'s entry claimed a defect `T-72` had fixed, and `§4.3` defined nine
invariants while the verifier enforced fifteen -- six of which existed only in
code, two of them added in the same session that failed to record them. Neither
was a behaviour bug. Both would have sent somebody looking for the wrong thing.

**What fails and what only reports.** A dangling identifier, a generated file
out of step with its source, a module claiming an unstarted task, or a
specification understating what the verifier checks are all objectively wrong
and fail. Whether a non-functional requirement deserves its own task, and
whether a partial catalogue entry needs a pinning xfail, are judgements -- they
are printed, so a reader sees them, and they do not block a merge on somebody
else's editorial opinion.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from vrp.bench.catalogue import declined, load

REPO = Path(__file__).resolve().parents[1]
SDD = REPO / "docs/vrp-spec-driven-development.md"
CATALOGUE = REPO / "docs/TDD/vrp-catalogue-v2.1.src.md"
EXAMPLES = REPO / "docs/planning/VRP_TDD_EXAMPLES.md"

SOURCES = sorted(
    path for pattern in ("vrp/**/*.py", "tests/**/*.py", "examples/**/*.py",
                         "parity/**/*.py")
    for path in REPO.glob(pattern) if "__pycache__" not in path.parts)


def _text(path: Path) -> str:
    return path.read_text()


def _defined() -> dict[str, set[str]]:
    sdd = _text(SDD)
    return {
        "FR": set(re.findall(r'^\| `(FR-\d+)` \|', sdd, re.MULTILINE)),
        "NFR": set(re.findall(r'^\| `(NFR-\d+)` \|', sdd, re.MULTILINE)),
        "T": set(re.findall(r'^\| `(T-\d+)` \|', sdd, re.MULTILINE)),
        "CON": set(re.findall(r'^### (CON-\d+)', sdd, re.MULTILINE)),
        "MTX": set(re.findall(r'^\| `(MTX-\d+)` \|', sdd, re.MULTILINE)),
        "INV": set(re.findall(r'^- `(INV-\d+)`', sdd, re.MULTILINE)),
    }


def _catalogue_ids() -> tuple[set[str], set[str]]:
    """Scenario ids the catalogue defines, and the ones §0.7 retires."""
    text = _text(CATALOGUE)
    live = set(re.findall(r'^\*\*`(UC-\d{3})`', text, re.MULTILINE))
    retired: set[str] = set()
    section = re.search(r'^### 0\.7 Retired identifiers\n(.*?)(?=^### )',
                        text, re.MULTILINE | re.DOTALL)
    if section:
        rows = "\n".join(line for line in section.group(1).splitlines()
                         if line.startswith("|"))
        for low, high in re.findall(r'`UC-(\d{3})`\s*[–-]\s*`UC-(\d{3})`', rows):
            retired |= {f"UC-{n:03d}" for n in range(int(low), int(high) + 1)}
        retired |= set(re.findall(r'`(UC-\d{3})`(?!\s*[–-])', rows))
    return live, retired


def _example_ids() -> set[str]:
    # Rows are written both plain and bold. Missing the bold ones reported four
    # perfectly good examples as undefined, which is the shape of false alarm
    # that teaches people to ignore a check.
    return set(re.findall(r'^\|\s*\*{0,2}`?(E-\d+[a-z]?)`?\*{0,2}\s*\|',
                          _text(EXAMPLES), re.MULTILINE))


# --------------------------------------------------------------------------
# Identifiers resolve
# --------------------------------------------------------------------------

def test_every_identifier_the_code_cites_is_defined_somewhere():
    """A citation is a promise that the reader can go and look it up."""
    defined = _defined()
    live, retired = _catalogue_ids()
    examples = _example_ids()
    dangling: list[str] = []

    for path in SOURCES:
        text = _text(path)
        where = path.relative_to(REPO)
        for kind in ("FR", "NFR", "T", "CON", "MTX", "INV"):
            for ref in sorted(set(re.findall(rf'\b{kind}-\d+\b', text))):
                # `FR-Pnn` are the catalogue's own proposals; §0.6 says a reader
                # who greps the design document for one will not find it.
                if ref.startswith("FR-P") or ref in defined[kind]:
                    continue
                dangling.append(f"{where}: {ref}")
        for ref in sorted(set(re.findall(r'\bUC-\d{3}\b', text))):
            if ref not in live and ref not in retired:
                dangling.append(f"{where}: {ref}")
        for ref in sorted(set(re.findall(r'\bE-\d+[a-z]?\b', text))):
            if ref not in examples:
                dangling.append(f"{where}: {ref}")

    assert not dangling, "identifiers cited by code and defined nowhere:\n  " + \
                         "\n  ".join(dangling)


def test_the_specification_names_every_invariant_the_verifier_enforces():
    """§4.3 listed nine while the verifier enforced fifteen, and §11.2 said so
    in as many words. A specification that understates what the system checks
    invites somebody to rely on a guarantee it does not know it makes."""
    enforced = set(re.findall(r'report\.fail\("(INV-\d+)"',
                              _text(REPO / "vrp/verify/verifier.py")))
    missing = sorted(enforced - _defined()["INV"],
                     key=lambda name: int(name.split("-")[1]))

    assert not missing, (
        f"the verifier enforces {missing} and §4.3 does not define them")


def test_section_11_2_states_the_range_it_actually_checks():
    claim = re.search(r'Checks (INV-1 . INV-(\d+))', _text(SDD))
    enforced = {int(name.split("-")[1]) for name in
                re.findall(r'report\.fail\("(INV-\d+)"',
                           _text(REPO / "vrp/verify/verifier.py"))}

    assert claim, "§11.2 no longer states which invariants the verifier checks"
    assert int(claim.group(2)) == max(enforced), (
        f"§11.2 claims {claim.group(1)}; the verifier goes to INV-{max(enforced)}")


# --------------------------------------------------------------------------
# Documents and files agree
# --------------------------------------------------------------------------

def test_the_generated_catalogue_matches_its_source():
    """`make catalogue` is the only writer. A hand-edit to the generated form
    is silently lost on the next run, which is how the source came to be
    missing entirely before `6341807`."""
    builder = REPO / "docs/TDD/build_catalogue.py"
    with tempfile.TemporaryDirectory() as tmp:
        out_md, out_jsonl = Path(tmp) / "out.md", Path(tmp) / "out.jsonl"
        result = subprocess.run(
            [sys.executable, str(builder), str(CATALOGUE), str(out_md),
             str(out_jsonl)], capture_output=True, text=True, cwd=REPO,
            check=False)   # the builder's own exit code is the assertion below
        assert result.returncode == 0, result.stdout + result.stderr
        for name, fresh in (("scenarios.jsonl", out_jsonl),
                            ("vrp-catalogue-v2.1.md", out_md)):
            committed = (REPO / "docs/TDD" / name).read_bytes()
            assert fresh.read_bytes() == committed, (
                f"{name} differs from a fresh build; run `make catalogue`")


def test_every_example_marked_done_points_at_a_file_that_exists():
    """A row naming a path nobody wrote sends a reader looking for it."""
    missing = []
    for line in _text(EXAMPLES).splitlines():
        row = re.match(r'^\|\s*\*{0,2}`?(E-\d+[a-z]?)`?\*{0,2}\s*\|\s*`([^`]+)`',
                       line)
        if row and "**Done" in line:
            target = row.group(2).split()[0]
            if not (REPO / target).exists():
                missing.append(f"{row.group(1)} -> {target}")
    assert not missing, "examples marked Done whose file is absent:\n  " + \
                        "\n  ".join(missing)


def test_no_module_claims_a_task_that_has_not_landed():
    """A docstring citing an unstarted task reads as provenance and is a
    forward reference nobody will come back to correct."""
    status = dict(re.findall(r'^\| `(T-\d+)` \| (\w+) \|', _text(SDD), re.MULTILINE))
    claims = []
    for path in SOURCES:
        head = _text(path)[:2000]
        for task in sorted(set(re.findall(r'\bT-\d+\b', head))):
            if status.get(task) not in (None, "done"):
                claims.append(f"{path.relative_to(REPO)}: {task} is "
                              f"{status[task]}")
    assert not claims, "modules citing unlanded tasks:\n  " + "\n  ".join(claims)


# --------------------------------------------------------------------------
# Reported, never failed
# --------------------------------------------------------------------------

def test_the_editorial_findings_are_reported(capsys):
    """Judgements, not defects, so a merge does not hang on somebody's
    editorial opinion -- but printed, so nobody has to run an audit by hand to
    see them."""
    sdd = _text(SDD)
    claimed: set[str] = set()
    for _task, satisfies in re.findall(
            r'^\| `(T-\d+)` \| \w+ \|[^|]*\|[^|]*\|([^|]*)\|', sdd, re.MULTILINE):
        claimed |= set(re.findall(r'\bN?FR-\d+\b', satisfies))
    defined = _defined()
    unclaimed = sorted((defined["FR"] | defined["NFR"]) - claimed)

    scenarios = load()
    pinned = {uc for block in re.findall(
        r'@pytest\.mark\.xfail\((.*?)\ndef ',
        "\n".join(_text(p) for p in SOURCES if p.name.startswith("test_")), re.DOTALL)
        for uc in re.findall(r'UC-\d{3}', block)}
    unpinned = sorted(s.id for s in declined(scenarios)
                      if s.status == "PARTIALLY_MODELLED" and s.id not in pinned)

    with capsys.disabled():
        print("\n\ntraceability — reported, not failed")
        print("-----------------------------------")
        print(f"  requirements no task claims: {unclaimed or 'none'}")
        print(f"  partial entries with no pinning xfail: {unpinned or 'none'}")

    assert isinstance(unclaimed, list) and isinstance(unpinned, list)


@pytest.mark.parametrize("document", [SDD, CATALOGUE, EXAMPLES])
def test_the_status_headers_carry_a_date_and_a_commit(document: Path):
    """§13's own rule: "a marker that cannot be dated is one nobody can trust".
    This does not check the numbers -- only that somebody committed to when
    they were true."""
    header = re.search(r'\*\*Status — [^*]+\*\*[^\n]*\n[^\n]*', _text(document))
    if header is None:
        pytest.skip(f"{document.name} carries no status header")
    assert re.search(r'20\d\d-\d\d-\d\d', header.group(0)), (
        f"{document.name}'s status header names no date")
    assert re.search(r'`[0-9a-f]{7,40}`', header.group(0)), (
        f"{document.name}'s status header names no commit")
