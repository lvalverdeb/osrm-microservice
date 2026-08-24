"""The OpenAPI snapshot the Rust gateway serves must stay in step.

`gateway/openapi.json` is FastAPI's own generated schema, committed so the Rust
binary can embed and serve it verbatim rather than re-describing every model in
`utoipa` annotations. That keeps one source of truth -- the pydantic models --
but only while something notices when the models move. This is that something.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

SNAPSHOT = Path(__file__).resolve().parents[1] / "gateway" / "openapi.json"


def current_schema() -> dict:
    """The schema FastAPI would generate right now."""
    return json.loads(json.dumps(app.openapi(), sort_keys=True))


def test_snapshot_exists():
    assert SNAPSHOT.exists(), f"{SNAPSHOT} is missing; run `make openapi-snapshot`"


def test_snapshot_matches_the_models():
    """Regenerate with `make openapi-snapshot` when this fails."""
    committed = json.loads(SNAPSHOT.read_text())
    assert committed == current_schema(), (
        "gateway/openapi.json is stale: the pydantic models changed but the "
        "snapshot the Rust gateway serves did not. Run `make openapi-snapshot`."
    )


def test_snapshot_carries_the_vrp_stop_ceiling():
    """The one assertion the Python suite already made about the schema.

    Note this checks the *document*. `tests/test_vrp_capacity.py` checks that
    the Python gateway enforces it, and the Rust unit tests check the same for
    the port -- a schema can advertise a limit no one enforces.
    """
    from app.config import settings
    committed = json.loads(SNAPSHOT.read_text())
    stops = committed["components"]["schemas"]["VrpRequest"]["properties"]["stops"]
    assert stops["maxItems"] == settings.VRP_MAX_STOPS
