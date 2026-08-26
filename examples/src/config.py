"""
Example settings loaded from .env via pydantic-settings.

Import `settings` to get typed config values.
Also exports values to os.environ so existing scripts using
os.environ.get("OSRM_API_URL") work without modification.
"""

import os as _os
import sys as _sys
from pathlib import Path as _Path

# The repository root, so an example can `import vrp` (the domain package used
# by the fleet examples). A script run from `examples/src/<area>/` gets its own
# directory on sys.path and not the root, so without this the import fails.
# Done here rather than per script: every example imports this module already.
_REPO_ROOT = _Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from pydantic_settings import BaseSettings, SettingsConfigDict


class ExamplesSettings(BaseSettings):
    OSRM_API_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=_Path(__file__).parent.parent / ".env",
        extra="ignore",
    )


settings = ExamplesSettings()

# Populate os.environ so legacy os.environ.get() calls pick up .env values
_os.environ.setdefault("OSRM_API_URL", settings.OSRM_API_URL)
