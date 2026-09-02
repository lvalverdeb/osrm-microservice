"""The versioned API surface — NFR-10, §9.4, T-90.

`NFR-10`: "The public API is versioned; breaking changes require a new major
version and a deprecation window." §9.4 writes the whole surface as `/v1/...`.
The gateway served it unversioned, so there was no way to make a breaking
change and keep a promise.

Black-box, against the compiled binary and the recorded upstream fixtures, so
these check what a client would see rather than what the router was told.
"""

from __future__ import annotations

import httpx
import pytest
from conftest_gateway import gateway, replay_engine, requires_binary

pytestmark = requires_binary

SUNSET = "Sun, 01 Mar 2026 00:00:00 GMT"

# One representative of each shape: a POST body endpoint, the nested VRP path,
# and the GET tile path whose segments are part of the route template.
PAIRS = (
    ("POST", "/route", "/v1/route"),
    ("POST", "/matrix", "/v1/matrix"),
    ("POST", "/nearest", "/v1/nearest"),
    ("POST", "/vrp/allocate", "/v1/vrp/allocate"),
)


@pytest.fixture(scope="module")
def live():
    with replay_engine() as engine, gateway(engine, API_SUNSET=SUNSET) as url:
        yield url


def body_for(path: str) -> dict:
    if path.endswith("/nearest"):
        return {"coordinates": [[-84.0, 9.9]]}
    if path.endswith("/vrp/allocate"):
        return {"depots": [{"id": "D", "coordinates": [-84.0, 9.9]}],
                "stops": [{"id": "S1", "coordinates": [-84.01, 9.91]}]}
    return {"coordinates": [[-84.0, 9.9], [-84.01, 9.91]]}


# --------------------------------------------------------------------------
# Both surfaces serve the same thing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method,plain,versioned", PAIRS)
def test_the_versioned_path_is_routed(live, method, plain, versioned):
    """Routed, not merely "not a 500".

    An endpoint the gateway does not serve answers with its own fallback body;
    a routed one answers with whatever the handler and the upstream produced.
    Comparing against the fallback distinguishes the two, which a bare status
    check cannot -- some of these fixtures legitimately 404 from upstream.
    """
    unrouted = httpx.post(f"{live}/definitely-not-an-endpoint", json={},
                          timeout=30.0)
    response = httpx.request(method, live + versioned, json=body_for(plain),
                             timeout=30.0)

    assert response.status_code < 500, response.text
    assert not (response.status_code == unrouted.status_code
                and response.text == unrouted.text), (
        f"{versioned} answered with the gateway's own not-found body, so it "
        "is not routed and §9.4's surface is not served")


@pytest.mark.parametrize("method,plain,versioned", PAIRS)
def test_both_spellings_return_the_same_body(live, method, plain, versioned):
    """The versioned path is the same endpoint, not a reimplementation."""
    old = httpx.request(method, live + plain, json=body_for(plain), timeout=30.0)
    new = httpx.request(method, live + versioned, json=body_for(plain),
                        timeout=30.0)

    assert old.status_code == new.status_code
    assert old.json() == new.json()


def test_the_tile_path_is_served_under_the_version_too(live):
    plain = httpx.get(f"{live}/tile/driving/12/1000/1000.mvt", timeout=30.0)
    versioned = httpx.get(f"{live}/v1/tile/driving/12/1000/1000.mvt",
                          timeout=30.0)
    assert plain.status_code == versioned.status_code
    assert plain.content == versioned.content


# --------------------------------------------------------------------------
# The deprecation window is announced
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method,plain,versioned", PAIRS)
def test_the_unversioned_response_says_it_is_deprecated(live, method, plain,
                                                        versioned):
    response = httpx.request(method, live + plain, json=body_for(plain),
                             timeout=30.0)

    assert response.headers.get("deprecation") == "true"
    assert response.headers.get("sunset") == SUNSET
    assert response.headers.get("link") == \
        f'<{versioned}>; rel="successor-version"'


@pytest.mark.parametrize("method,plain,versioned", PAIRS)
def test_the_versioned_response_does_not_nag(live, method, plain, versioned):
    """A client that migrated must stop being told to migrate."""
    response = httpx.request(method, live + versioned, json=body_for(plain),
                             timeout=30.0)

    assert "deprecation" not in response.headers
    assert "sunset" not in response.headers
    assert "successor-version" not in response.headers.get("link", "")


def test_an_unrouted_path_is_not_told_to_migrate(live):
    """The advisory has to point somewhere.

    `/nonsense` has no `/v1` successor, and answering a client's typo with
    "use /v1/nonsense" invents an endpoint.
    """
    response = httpx.post(f"{live}/nonsense", json={}, timeout=30.0)

    assert response.status_code == 404
    assert "deprecation" not in response.headers
    assert "successor-version" not in response.headers.get("link", "")


def test_without_a_configured_date_no_sunset_is_invented():
    """NFR-10 wants a window; only an operator knows when it closes. The
    deprecation is still announced, so the absence of a date is not the absence
    of a warning."""
    with replay_engine() as engine, gateway(engine) as url:
        response = httpx.post(f"{url}/nearest",
                              json={"coordinates": [[-84.0, 9.9]]}, timeout=30.0)

    assert response.headers.get("deprecation") == "true"
    assert "sunset" not in response.headers


# --------------------------------------------------------------------------
# Operational paths are not part of the versioned contract
# --------------------------------------------------------------------------

def test_the_probes_stay_where_orchestrators_expect_them(live):
    """Versioning `/health` would break every liveness probe and Prometheus job
    for a promise nobody asked for."""
    for path in ("/health", "/ready"):
        assert httpx.get(live + path, timeout=30.0).status_code == 200
        assert httpx.get(live + "/v1" + path, timeout=30.0).status_code == 404


@pytest.mark.parametrize("path", ["/health", "/ready", "/openapi.json", "/docs"])
def test_the_unversioned_by_design_paths_are_not_nagged(live, path):
    """They are unversioned by design rather than awaiting migration.

    An orchestrator's liveness probe is not a client integration, and a
    `Deprecation` header on `/health` is advice with nowhere to go — the bug
    this test was written after finding.
    """
    response = httpx.get(live + path, timeout=30.0)
    assert "deprecation" not in response.headers
    assert "sunset" not in response.headers
