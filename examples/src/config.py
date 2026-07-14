"""
Example settings loaded from .env via pydantic-settings.

Import `settings` to get typed config values.
Also exports values to os.environ so existing scripts using
os.environ.get("OSRM_API_URL") work without modification.
"""

import os as _os
from pathlib import Path as _Path

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
