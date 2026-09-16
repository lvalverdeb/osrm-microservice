"""The generated documents and the sources they are read from.

`make tier1` is the only writer. A hand edit to anything in `docs/tier1/` is
silently lost on the next run and, until then, describes a system that is not
this one -- the same failure `test_the_generated_catalogue_matches_its_source`
exists to catch one directory over. So the suite regenerates the whole set into
a temporary directory and compares it byte for byte.

The build starts the gateway to read its `/openapi.json`, so this skips without
the binary and, under CI, fails rather than skipping.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from conftest_gateway import requires_binary

REPO = Path(__file__).resolve().parents[1]
TIER1 = REPO / "docs/tier1"


def _built(destination: Path) -> dict[str, bytes]:
    """Run the generator into `destination` and read back what it wrote."""
    result = subprocess.run(
        [sys.executable, str(TIER1 / "build.py"), str(destination)],
        capture_output=True, text=True, cwd=REPO, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return {path.name: path.read_bytes() for path in destination.glob("*.md")}


@requires_binary
def test_the_generated_documents_match_their_sources():
    """A committed document out of step with the code is worse than none."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = _built(Path(tmp))
    committed = {path.name: path.read_bytes() for path in TIER1.glob("*.md")}

    assert sorted(fresh) == sorted(committed), "the set of documents changed"
    stale = sorted(name for name, content in fresh.items()
                   if content != committed[name])
    assert not stale, f"differ from a fresh build; run `make tier1`: {stale}"


@requires_binary
def test_the_build_is_reproducible():
    """Two builds of one tree must agree, or the freshness check is noise."""
    with tempfile.TemporaryDirectory() as first, \
         tempfile.TemporaryDirectory() as second:
        assert _built(Path(first)) == _built(Path(second))
