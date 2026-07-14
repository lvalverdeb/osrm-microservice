import os
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
