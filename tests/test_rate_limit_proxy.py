"""Rate limiting behind a proxy.

`app.main` keys limits on `get_remote_address`, i.e. the immediate TCP peer.
Behind a reverse proxy that peer is the proxy, so every client would share one
bucket. uvicorn's ProxyHeadersMiddleware fixes that by rewriting the client from
`X-Forwarded-For` -- but only for peers named in `--forwarded-allow-ips`, which
the deployments pass (deploy/docker/entrypoint.sh, deploy/freebsd/osrm-api-gateway).

These tests build their own app and their own Limiter. `app.main` exposes both as
module-level singletons shared by every test module, and nothing resets limiter
counters between tests, so exercising the real limiter here would leak state into
unrelated tests.
"""

import httpx
import pytest
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# ASGITransport presents this as the peer address, standing in for the hop that
# would really connect -- a proxy when it is trusted, a direct client when not.
PEER = "127.0.0.1"


def _build_app(limit: str = "2/minute") -> FastAPI:
    """A minimal app with its own limiter, isolated from app.main's."""
    limiter = Limiter(key_func=get_remote_address)
    application = FastAPI()
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @application.get("/probe")
    @limiter.limit(limit)
    async def probe(request: Request) -> dict[str, str]:
        return {"client": request.client.host if request.client else "none"}

    return application


def _client(application: FastAPI, trusted: str | None) -> httpx.AsyncClient:
    """Wrap the app the way uvicorn would when --forwarded-allow-ips is passed."""
    asgi = application if trusted is None else ProxyHeadersMiddleware(application, trusted_hosts=trusted)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url="http://test")


@pytest.mark.asyncio
async def test_forwarded_for_ignored_when_peer_is_not_trusted():
    """The default posture: a spoofed header must not change the limiter key."""
    async with _client(_build_app(), trusted="10.0.0.0/8") as c:
        body = (await c.get("/probe", headers={"X-Forwarded-For": "203.0.113.7"})).json()
    assert body["client"] == PEER


@pytest.mark.asyncio
async def test_forwarded_for_honoured_when_peer_is_trusted():
    async with _client(_build_app(), trusted=PEER) as c:
        body = (await c.get("/probe", headers={"X-Forwarded-For": "203.0.113.7"})).json()
    assert body["client"] == "203.0.113.7"


@pytest.mark.asyncio
async def test_client_supplied_hop_does_not_win():
    """The key is the closest untrusted hop, not the leftmost entry.

    A client can prepend anything to X-Forwarded-For. uvicorn walks the list from
    the right and stops at the first host outside the trusted set, so the value
    the client injected is skipped in favour of what the proxy appended.
    """
    async with _client(_build_app(), trusted=f"{PEER},10.0.0.9") as c:
        body = (await c.get(
            "/probe",
            headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7, 10.0.0.9"},
        )).json()
    assert body["client"] == "203.0.113.7"


@pytest.mark.asyncio
async def test_each_forwarded_client_gets_its_own_bucket():
    """Two clients behind one proxy must not consume each other's allowance."""
    async with _client(_build_app("2/minute"), trusted=PEER) as c:
        a = [(await c.get("/probe", headers={"X-Forwarded-For": "203.0.113.1"})).status_code
             for _ in range(3)]
        b = [(await c.get("/probe", headers={"X-Forwarded-For": "203.0.113.2"})).status_code
             for _ in range(2)]
    assert a == [200, 200, 429], a
    assert b == [200, 200], b


@pytest.mark.asyncio
async def test_without_proxy_awareness_clients_share_one_bucket():
    """The behaviour being fixed, pinned so a regression is visible.

    With no trusted proxy configured, distinct forwarded clients all key on the
    peer -- so the third request is rejected even though it is the first from
    that client.
    """
    async with _client(_build_app("2/minute"), trusted=None) as c:
        codes = [(await c.get("/probe", headers={"X-Forwarded-For": f"203.0.113.{i}"})).status_code
                 for i in range(3)]
    assert codes == [200, 200, 429], codes
