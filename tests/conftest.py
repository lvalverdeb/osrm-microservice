import pytest
import httpx


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests against a real OSRM backend",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: tests that require a running OSRM backend")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(reason="use --run-integration to enable")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


@pytest.fixture(scope="session")
def osrm_available():
    try:
        resp = httpx.get("http://localhost:5000/route/v1/driving/0,0;0,0", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False
