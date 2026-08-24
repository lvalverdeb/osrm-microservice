"""A stand-in for `osrm-routed`, in two modes.

**record** forwards to a real engine and saves what comes back. Point a gateway
at this instead of the engine, run the corpus, and the fixtures accumulate.

**replay** serves those fixtures and nothing else. A request with no fixture
gets a 404 naming the path it asked for, which is deliberate: a miss means the
gateway under test built a different upstream request than the one recorded, and
that is a parity failure worth seeing rather than a blank response to puzzle over.

    uv run python -m parity.engine --mode record --engine http://127.0.0.1:5000
    uv run python -m parity.engine --mode replay

Both modes are also importable as an ASGI app, which is how the offline replay
test drives them without a socket.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from parity.upstream import DEFAULT_FIXTURE_DIR, Fixture, FixtureStore

# 404, deliberately not 5xx. The gateway retries 5xx with exponential backoff,
# so a miss reported that way is retried three times before surfacing -- which
# triples the recorded misses, hides the real count, and makes a failing replay
# run take its full backoff budget. A 4xx passes straight through: one miss
# recorded per request, reported immediately. The body names it either way.
MISS_STATUS = 404


def build_app(mode: str, store: FixtureStore, engine_url: str | None = None,
              client: httpx.AsyncClient | None = None) -> Starlette:
    """Build the ASGI app for `record` or `replay`.

    Args:
        mode: `record` or `replay`.
        store: Where fixtures are read from or written to.
        engine_url: The real engine, required for `record`.
        client: Optional client used to reach the engine. Injected rather than
            constructed so a caller can record against an in-process engine over
            `ASGITransport`, with no sockets. Left unset, a client is built per
            request against `engine_url`.

    Returns:
        A Starlette app answering every path.

    Raises:
        ValueError: If `record` is requested without an engine URL.
    """
    if mode == "record" and not engine_url:
        raise ValueError("record mode needs --engine")

    async def handle(request: Request) -> Response:
        path = request.url.path
        query = request.url.query
        if mode == "replay":
            return _replay(store, request.method, path, query)
        return await _record(store, engine_url, request.method, path, query, client)

    return Starlette(routes=[Route("/{rest:path}", handle, methods=["GET"])])


def _replay(store: FixtureStore, method: str, path: str, query: str) -> Response:
    """Serve a recorded response, or report the miss loudly."""
    fixture = store.load(method, path, query)
    if fixture is None:
        return Response(
            json.dumps({"code": "FixtureMiss", "message": f"no fixture for {path}?{query}"}),
            status_code=MISS_STATUS,
            media_type="application/json",
        )
    return Response(fixture.body, status_code=fixture.status, media_type=fixture.content_type)


async def _record(store: FixtureStore, engine_url: str, method: str, path: str,
                  query: str, client: httpx.AsyncClient | None) -> Response:
    """Forward to the real engine and save what it returns."""
    url = f"{engine_url.rstrip('/')}{path}"
    if client is not None:
        upstream = await client.request(method, url, params=query)
    else:
        async with httpx.AsyncClient(timeout=60.0) as owned:
            upstream = await owned.request(method, url, params=query)
    fixture = Fixture(
        status=upstream.status_code,
        content_type=upstream.headers.get("content-type", "application/json").split(";")[0],
        body=upstream.content,
        method=method,
        path=path,
        query=query,
    )
    store.save(fixture)
    return Response(fixture.body, status_code=fixture.status, media_type=fixture.content_type)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["record", "replay"], default="replay")
    parser.add_argument("--engine", default=None,
                        help="the real osrm-routed, required by record mode")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5599)
    return parser.parse_args(argv)


def main() -> int:
    import uvicorn

    args = parse_args()
    store = FixtureStore(args.fixtures)
    app = build_app(args.mode, store, args.engine)
    print(f"parity engine [{args.mode}] on {args.host}:{args.port}  "
          f"fixtures={args.fixtures} ({store.count()} stored)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
