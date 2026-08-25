"""Running a real routing engine over the hand-built map in tests/synthetic/.

The recorded fixtures in `parity/fixtures/` key on the outgoing URL and replay
what was recorded, so a gateway that builds a well formed but *semantically*
wrong upstream request -- coordinates transposed, sources and destinations
swapped -- still gets a plausible answer. Only a real engine over a map whose
geometry is known can catch that, which is why this exists alongside them rather
than instead of them.

The map is small enough that extract/partition/customize take about a second, so
the data is built per session rather than committed.

Two backends, because neither covers both places these need to run: the OSRM
binaries if they are on PATH (a developer on macOS via `brew install
osrm-backend`), otherwise Docker (CI, where the toolchain is not packaged).
Tests skip when neither is available.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
MAP = REPO / "tests" / "synthetic" / "grid.osm"
OSRM_IMAGE = "ghcr.io/project-osrm/osrm-backend:latest"

# Where the car profile lives in each backend.
DOCKER_PROFILE = "/opt/car.lua"
LOCAL_PROFILE_CANDIDATES = (
    "/opt/homebrew/share/osrm/profiles/car.lua",
    "/usr/local/share/osrm/profiles/car.lua",
    "/usr/share/osrm/profiles/car.lua",
)


def local_profile() -> str | None:
    """The stock car profile shipped with a local OSRM install."""
    return next((p for p in LOCAL_PROFILE_CANDIDATES if Path(p).exists()), None)


def backend() -> str | None:
    """Which engine backend is usable here: `local`, `docker`, or neither."""
    if shutil.which("osrm-extract") and shutil.which("osrm-routed") and local_profile():
        return "local"
    if shutil.which("docker") and subprocess.run(
            ["docker", "info"], capture_output=True, check=False).returncode == 0:
        return "docker"
    return None


requires_engine = pytest.mark.skipif(
    backend() is None,
    reason="needs either the OSRM binaries on PATH (brew install osrm-backend) or Docker",
)


def build_data(workdir: Path) -> None:
    """Run extract, partition and customize over the synthetic map."""
    shutil.copy(MAP, workdir / "grid.osm")
    if backend() == "local":
        steps = [
            ["osrm-extract", "-p", local_profile(), "grid.osm"],
            ["osrm-partition", "grid.osrm"],
            ["osrm-customize", "grid.osrm"],
        ]
        for step in steps:
            subprocess.run(step, cwd=workdir, check=True, capture_output=True)
        return

    mount = ["-v", f"{workdir}:/data"]
    for step in (["osrm-extract", "-p", DOCKER_PROFILE, "/data/grid.osm"],
                 ["osrm-partition", "/data/grid.osrm"],
                 ["osrm-customize", "/data/grid.osrm"]):
        subprocess.run(["docker", "run", "--rm", *mount, OSRM_IMAGE, *step],
                       check=True, capture_output=True)


@contextlib.contextmanager
def routing_engine(workdir: Path):
    """Serve the built map and yield the engine's base URL."""
    from conftest_gateway import free_port

    port = free_port()
    if backend() == "local":
        process = subprocess.Popen(
            ["osrm-routed", "--algorithm", "mld", "grid.osrm", "--port", str(port)],
            cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stop = process.terminate
    else:
        name = f"osrm-synthetic-{port}"
        subprocess.run(
            ["docker", "run", "--rm", "-d", "--name", name, "-p", f"{port}:5000",
             "-v", f"{workdir}:/data", OSRM_IMAGE,
             "osrm-routed", "--algorithm", "mld", "/data/grid.osrm"],
            check=True, capture_output=True)
        stop = lambda: subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                httpx.get(f"{url}/route/v1/driving/0,0;0.01,0", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            raise RuntimeError(f"routing engine did not start at {url}")
        yield url
    finally:
        stop()
