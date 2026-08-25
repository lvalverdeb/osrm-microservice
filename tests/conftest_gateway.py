"""Running the Rust gateway from pytest, against recorded fixtures.

The parity harness used to drive the FastAPI app in-process over ASGI. With that
implementation gone, the only gateway is a compiled binary, so the harness's own
tests start it as a subprocess instead -- pointed at the replay engine, so they
still need no routing engine and no network.

Tests using these skip when the binary has not been built, which keeps `pytest`
usable without a Rust toolchain. CI builds it in the `rust` job, and the
`parity-selfcheck` make target builds it first.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
BINARY = REPO / "gateway" / "target" / "debug" / "osrm-api-gateway"
FIXTURES = REPO / "parity" / "fixtures"


def free_port() -> int:
    """Claim a port the OS says is free."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_ready(url: str, deadline: float = 20.0) -> None:
    """Block until the gateway answers, or fail with what it did instead."""
    started = time.monotonic()
    last = "no attempt made"
    while time.monotonic() - started < deadline:
        try:
            httpx.get(f"{url}/health", timeout=2)
            return
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"gateway did not become reachable at {url}: {last}")


@contextlib.contextmanager
def gateway(engine_url: str, **env: str):
    """Run the gateway against `engine_url` and yield its base URL."""
    require_binary_built()
    port = free_port()
    environment = {
        **os.environ,
        "OSRM_BASE_URL": engine_url,
        "HOST": "127.0.0.1",
        "PORT": str(port),
        # Rate limits are per client address and every request here comes from
        # one, so the deployed limits would shed a corpus run partway through.
        **{f"RATE_LIMIT_{name}": "1000000/minute" for name in
           ("ROUTE", "MATRIX", "MATCH", "TRIP", "VRP", "NEAREST", "TILE")},
        **env,
    }
    process = subprocess.Popen([str(BINARY)], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_until_ready(url)
        yield url
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)


@contextlib.contextmanager
def replay_engine(fixtures: Path = FIXTURES / "upstream"):
    """Serve the recorded upstream fixtures, and yield the engine's base URL.

    A subprocess rather than an in-process ASGI app, because the gateway under
    test is a binary that speaks HTTP.
    """
    port = free_port()
    process = subprocess.Popen(
        ["uv", "run", "python", "-m", "parity.engine", "--mode", "replay",
         "--fixtures", str(fixtures), "--port", str(port)],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                # Any path answers; a miss is a 404, which still proves it is up.
                httpx.get(f"{url}/ping", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError(f"replay engine did not start at {url}")
        yield url
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)


# Skipping locally is a convenience: `pytest` stays usable without a Rust
# toolchain. Skipping in CI is a hole -- these are the harness's own acceptance
# test and the replay regression gate, and a green run that never executed them
# says nothing. So CI fails instead, which is what catches the build step being
# dropped from the workflow.
IN_CI = os.environ.get("CI", "").lower() == "true"

requires_binary = pytest.mark.skipif(
    not BINARY.exists() and not IN_CI,
    reason=f"{BINARY.relative_to(REPO)} not built; run `cargo build --manifest-path gateway/Cargo.toml`",
)


def require_binary_built() -> None:
    """Fail with an actionable message when the binary is missing under CI."""
    if not BINARY.exists():
        raise AssertionError(
            f"{BINARY.relative_to(REPO)} is missing. In CI these tests must run, not skip: "
            "the workflow needs a `cargo build --manifest-path gateway/Cargo.toml` step "
            "before pytest.")
