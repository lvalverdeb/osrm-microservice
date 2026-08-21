from pathlib import Path

from app.config import Settings


def test_config_defaults():
    settings = Settings()
    assert settings.OSRM_BASE_URL == "http://localhost:5000"
    assert settings.APP_NAME == "OSRM API Gateway"
    assert settings.DEBUG is False
    assert settings.RATE_LIMIT_ROUTE == "600/minute"
    assert settings.RATE_LIMIT_VRP == "100/minute"


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("OSRM_BASE_URL", "http://osrm:5000")
    monkeypatch.setenv("APP_NAME", "Test Gateway")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("RATE_LIMIT_ROUTE", "10/minute")
    settings = Settings()
    assert settings.OSRM_BASE_URL == "http://osrm:5000"
    assert settings.APP_NAME == "Test Gateway"
    assert settings.DEBUG is True
    assert settings.RATE_LIMIT_ROUTE == "10/minute"


def test_config_rate_limits_are_valid():
    settings = Settings()
    for key in dir(settings):
        if key.startswith("RATE_LIMIT_"):
            val = getattr(settings, key)
            assert "/" in val or val == "", f"Invalid rate limit format for {key}: {val}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _parse_env(path: Path) -> dict[str, str]:
    """Minimal dotenv parse: last occurrence of a key wins, as dotenv does."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


SHARED_ENV = _repo_root() / "deploy" / "env" / "app.env"


def test_shared_env_file_exists():
    """Docker's compose `env_file:` hard-errors if this file is missing."""
    assert SHARED_ENV.is_file(), f"{SHARED_ENV} is loaded by both deployments"


def test_shared_env_keys_are_all_real_settings():
    """Guards against typos and against settings drifting out of the shared file."""
    fields = set(Settings.model_fields)
    unknown = set(_parse_env(SHARED_ENV)) - fields
    assert not unknown, f"not fields on Settings: {sorted(unknown)}"


def test_shared_env_covers_every_setting():
    """A new setting must be added to the shared file, or neither deployment sees it."""
    missing = set(Settings.model_fields) - set(_parse_env(SHARED_ENV))
    assert not missing, f"missing from deploy/env/app.env: {sorted(missing)}"


def test_shared_env_holds_no_deployment_knobs():
    """Deployment-specific values belong in deploy/{docker,freebsd}/.env.example."""
    keys = _parse_env(SHARED_ENV)
    leaked = [
        k for k in keys
        if k.startswith(("JAIL_", "LOADTEST_")) or k in {"DOCKER_HOST", "API_PORT", "OSRM_PORT", "PROFILE", "OSM_FILE", "UV_PUBLISH_TOKEN"}
    ]
    assert not leaked, f"deployment knobs in the shared app file: {leaked}"


def test_shared_env_values_match_code_defaults():
    """The shared file is the baseline; it must not silently redefine behaviour."""
    defaults = Settings()
    for key, raw in _parse_env(SHARED_ENV).items():
        expected = getattr(defaults, key)
        actual = type(expected)(raw) if not isinstance(expected, bool) else raw.lower() == "true"
        assert actual == expected, f"{key}: file has {actual!r}, code default is {expected!r}"


def test_dotenv_last_key_wins(tmp_path, monkeypatch):
    """The jail appends its overlay after the shared block and relies on this.

    deploy/freebsd/install.sh writes deploy/env/app.env followed by an override
    block carrying OSRM_BASE_URL/REDIS_URL. If an earlier duplicate won instead,
    the jail would silently talk to the shared file's localhost URL.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OSRM_BASE_URL=http://shared:5000\n"
        "REDIS_URL=\n"
        "\n# --- overlay ---\n"
        "OSRM_BASE_URL=http://overlay:5000\n"
        "REDIS_URL=redis://overlay:6379/0\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OSRM_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings()
    assert settings.OSRM_BASE_URL == "http://overlay:5000"
    assert settings.REDIS_URL == "redis://overlay:6379/0"


def test_process_env_outranks_env_file(tmp_path, monkeypatch):
    """Tier 3: a real env var beats the file on both deployment paths."""
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=From File\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_NAME", "From Process Env")
    assert Settings().APP_NAME == "From Process Env"
