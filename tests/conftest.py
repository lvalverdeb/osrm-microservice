"""Fixtures shared across the test suite.

Lives here rather than beside its helpers because pytest only collects fixtures
from `conftest.py`; importing one from a plain module and then naming it as a
test parameter reads as a redefinition to linters, and does not register it.
"""

from __future__ import annotations

import pytest
from conftest_gateway import gateway
from conftest_synthetic import build_data, routing_engine


@pytest.fixture(scope="session")
def synthetic_gateway(tmp_path_factory):
    """A gateway serving tests/synthetic/grid.osm through a real routing engine.

    Session-scoped: extract, partition and customize run once, which is about a
    second for a map this size, and every test then shares one engine.
    """
    workdir = tmp_path_factory.mktemp("synthetic-osrm")
    build_data(workdir)
    with routing_engine(workdir) as engine, gateway(engine) as url:
        yield url
