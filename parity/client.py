"""Sending one corpus case to one gateway.

Responses are normalised into a single record so the comparator never has to
know whether a body arrived as JSON or as protobuf bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from parity.corpus import Case

# Statuses that mean the harness is misconfigured rather than the port wrong:
# the limiter shed the request, or the VRP admission gate did.
PRECONDITION_STATUSES = frozenset({429, 503})


@dataclass(frozen=True)
class Reply:
    """One gateway's answer to one case."""

    status: int
    content_type: str
    json: Any | None = None
    raw: bytes | None = None
    transport_error: str | None = None

    @property
    def is_precondition_failure(self) -> bool:
        """True when the run's environment, not the port, is at fault."""
        return self.transport_error is not None or self.status in PRECONDITION_STATUSES


async def send(client: httpx.AsyncClient, case: Case, want_bytes: bool) -> Reply:
    """Send one case and normalise the response.

    Args:
        client: An `httpx.AsyncClient` bound to one gateway's base URL.
        case: The request to send.
        want_bytes: True for endpoints whose body is not JSON (`/tile`).

    Returns:
        The normalised reply. A transport failure is recorded rather than
        raised, so one unreachable gateway does not abort the whole run.
    """
    try:
        response = await client.request(case.method, case.path, json=case.body)
    except httpx.HTTPError as exc:
        return Reply(status=0, content_type="", transport_error=f"{type(exc).__name__}: {exc}")

    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if want_bytes:
        return Reply(response.status_code, content_type, raw=response.content)
    return Reply(response.status_code, content_type, json=_decode(response))


def _decode(response: httpx.Response) -> Any | None:
    """Decode a JSON body, or None when it is not JSON at all."""
    try:
        return response.json()
    except ValueError:
        return None
