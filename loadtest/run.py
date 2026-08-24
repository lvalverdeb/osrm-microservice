"""Open-model load generator for the OSRM API Gateway.

Requests are launched on a fixed schedule rather than after the previous one
returns, so a slow server shows up as rising latency instead of a quietly lower
request rate -- the coordinated-omission trap that closed-loop tools like `ab`
and `wrk` fall into.

Every payload is randomised, which also defeats the gateway's L1/Redis caches:
replaying one payload measures the cache, not the service. That is the right
default, but cache hits are a real workload too -- and the one where the gateway
is the whole request rather than a thin wrapper around the engine. Draw from a
small fixed set of payloads instead with `--distinct-payloads N`.

Every endpoint the gateway exposes has a scenario, and `mixed` fires a weighted
blend of them concurrently, which is what a real client population looks like.

Usage:
    uv run python loadtest/run.py --url http://127.0.0.1:8000 \
        --scenario mixed --rate 25 --duration 30

Thresholds turn a run into a pass/fail gate:
    ... --max-p95 0.5 --max-error-rate 0.01
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

# Greater San Jose: dense enough that random points snap to real roads.
LON_MIN, LON_MAX = -84.13, -84.01
LAT_MIN, LAT_MAX = 9.90, 9.99

# osrm-routed's --max-table-size defaults to 100; larger matrices get a 400.
MATRIX_MAX_COORDINATES = 100

# OSRM serves vector tiles from zoom 12 up.
TILE_MIN_ZOOM = 12

# A real road-network trace, taken from a /route response. Map matching against
# random points fails by design (OSRM answers NoMatch), so the match scenario
# jitters these by a few metres instead -- the shape of actual GPS noise.
ROAD_TRACE: tuple[tuple[float, float], ...] = (
    (-84.090271, 9.928567),
    (-84.089638, 9.928354),
    (-84.089258, 9.929893),
    (-84.088575, 9.929689),
    (-84.086127, 9.929715),
    (-84.084296, 9.929814),
    (-84.084154, 9.933825),
)

# A request is (method, path, json body or None).
Request = tuple[str, str, dict[str, Any] | None]
Builder = Callable[[random.Random, int], Request]


@dataclass(frozen=True)
class Sample:
    """One completed request."""

    path: str
    status: int
    seconds: float


def _point(rng: random.Random) -> dict[str, float]:
    """Return a random coordinate inside the test bounding box."""
    return {
        "longitude": round(rng.uniform(LON_MIN, LON_MAX), 6),
        "latitude": round(rng.uniform(LAT_MIN, LAT_MAX), 6),
    }


def _coordinates(rng: random.Random, count: int) -> list[dict[str, float]]:
    """Return `count` random coordinates."""
    return [_point(rng) for _ in range(count)]


def _stops(rng: random.Random, count: int) -> list[dict[str, Any]]:
    """Return `count` identified stops for the VRP endpoints."""
    return [dict(_point(rng), id=f"s{i}") for i in range(max(1, count))]


def build_health(rng: random.Random, size: int) -> Request:
    """Health probe: also exercises the OSRM ping path."""
    return "GET", "/health", None


def build_metrics(rng: random.Random, size: int) -> Request:
    """Prometheus scrape, as a monitoring system would issue it."""
    return "GET", "/metrics", None


def build_route(rng: random.Random, size: int) -> Request:
    """Route with `size` intermediate waypoints."""
    payload: dict[str, Any] = {
        "origin": _point(rng),
        "destination": _point(rng),
        "steps": False,
        "overview": "simplified",
    }
    if size:
        payload["waypoints"] = _coordinates(rng, size)
    return "POST", "/route", payload


def build_nearest(rng: random.Random, size: int) -> Request:
    """Snap one coordinate to the road network."""
    return "POST", "/nearest", {"coordinate": _point(rng), "number": max(1, size)}


def build_matrix(rng: random.Random, size: int) -> Request:
    """Distance/duration matrix, clamped to what the engine accepts."""
    count = max(2, min(size, MATRIX_MAX_COORDINATES))
    return "POST", "/matrix", {"coordinates": _coordinates(rng, count)}


def build_matrix_graph(rng: random.Random, size: int) -> Request:
    """Same payload as /matrix, returned as a node/edge graph."""
    count = max(2, min(size, MATRIX_MAX_COORDINATES))
    return "POST", "/matrix-graph", {"coordinates": _coordinates(rng, count)}


def build_trip(rng: random.Random, size: int) -> Request:
    """TSP over `size` coordinates."""
    return "POST", "/trip", {
        "coordinates": _coordinates(rng, max(2, size)),
        "roundtrip": True,
    }


def build_match(rng: random.Random, size: int) -> Request:
    """Map-match a jittered version of a known-good road trace."""
    breadcrumbs = [
        {
            "longitude": round(lon + rng.uniform(-0.00008, 0.00008), 6),
            "latitude": round(lat + rng.uniform(-0.00008, 0.00008), 6),
            "timestamp": 1700000000 + index * 15,
        }
        for index, (lon, lat) in enumerate(ROAD_TRACE)
    ]
    return "POST", "/match", {"breadcrumbs": breadcrumbs}


def build_tile(rng: random.Random, size: int) -> Request:
    """Vector tile covering a random point in the bounding box."""
    zoom = max(TILE_MIN_ZOOM, size)
    lon = rng.uniform(LON_MIN, LON_MAX)
    lat = rng.uniform(LAT_MIN, LAT_MAX)
    scale = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * scale)
    latitude = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(latitude)) / math.pi) / 2.0 * scale)
    return "GET", f"/tile/car/{zoom}/{x}/{y}.mvt", None


def build_vrp(rng: random.Random, size: int) -> Request:
    """Full VRP solve: one depot, `size` stops."""
    return "POST", "/vrp", {
        "depots": [dict(_point(rng), id="d1")],
        "stops": _stops(rng, size),
        "capacity": 50,
    }


def build_vrp_allocate(rng: random.Random, size: int) -> Request:
    """Allocation phase only, across two depots."""
    return "POST", "/vrp/allocate", {
        "depots": [dict(_point(rng), id="d1"), dict(_point(rng), id="d2")],
        "stops": _stops(rng, size),
        "capacity": 50,
    }


BUILDERS: dict[str, Builder] = {
    "health": build_health,
    "metrics": build_metrics,
    "route": build_route,
    "nearest": build_nearest,
    "matrix": build_matrix,
    "matrix-graph": build_matrix_graph,
    "trip": build_trip,
    "match": build_match,
    "tile": build_tile,
    "vrp": build_vrp,
    "vrp-allocate": build_vrp_allocate,
}

# Per-scenario payload size: waypoints, coordinates, stops or zoom level.
DEFAULT_SIZE: dict[str, int] = {
    "health": 0, "metrics": 0, "route": 0, "nearest": 1, "matrix": 25,
    "matrix-graph": 25, "trip": 5, "match": 0, "tile": 13, "vrp": 50,
    "vrp-allocate": 50, "mixed": 0,
}

# Weighted blend for `mixed`, roughly a delivery-planning client population:
# lots of routing, occasional optimisation, monitoring in the background.
MIXED_WEIGHTS: dict[str, int] = {
    "route": 35, "nearest": 15, "matrix": 12, "tile": 10, "trip": 8,
    "match": 8, "matrix-graph": 5, "vrp-allocate": 3, "vrp": 2,
    "health": 1, "metrics": 1,
}

SCENARIOS = sorted(BUILDERS) + ["mixed"]


def _build(rng: random.Random, scenario: str, size: int) -> Request:
    """Build one request for `scenario`, choosing an endpoint when mixed."""
    if scenario != "mixed":
        return BUILDERS[scenario](rng, size)
    name = rng.choices(list(MIXED_WEIGHTS), weights=list(MIXED_WEIGHTS.values()))[0]
    return BUILDERS[name](rng, DEFAULT_SIZE[name])


def _payload_source(rng: random.Random, plan: Plan) -> Callable[[int], Request]:
    """Return the function that supplies each request's payload.

    With `plan.distinct_payloads` at 0 every request is freshly randomised, so
    the gateway's caches never hit and the run measures the engine path. Above
    0, a pool of that many payloads is built once and cycled deterministically:
    after the first pass every request is a cache hit, which measures the
    gateway alone. Cycling rather than sampling keeps the pool's entries equally
    warm, so none expires out of an LRU while others are hammered.

    Args:
        rng: Seeded source for payload contents.
        plan: The phase being run; supplies scenario, size and pool size.

    Returns:
        A callable taking the request's index within the phase and returning
        the request to send.
    """
    if plan.distinct_payloads <= 0:
        return lambda issued: _build(rng, plan.scenario, plan.size)
    pool = [_build(rng, plan.scenario, plan.size)
            for _ in range(plan.distinct_payloads)]
    return lambda issued: pool[issued % len(pool)]


async def _fire(client: httpx.AsyncClient, request: Request, out: list[Sample],
                headers: dict[str, str] | None = None) -> None:
    """Send one request and record its path, status and wall-clock duration.

    Transport failures are recorded as status 0 so they surface in the report
    rather than cancelling the run.
    """
    method, path, payload = request
    started = time.perf_counter()
    try:
        response = await client.request(method, path, json=payload, headers=headers)
        status = response.status_code
    except httpx.HTTPError:
        status = 0
    out.append(Sample(_label(path), status, time.perf_counter() - started))


# 198.18.0.0/15, the RFC 2544 benchmarking range.
_FORWARDED_FOR_MAX = 1 << 17


def _forwarded_for(pool: int, issued: int) -> dict[str, str] | None:
    """Spread requests across `pool` synthetic client addresses.

    Rate limits are keyed per client address, so every request from one source
    lands in one bucket and the run measures the limiter rather than the server.
    Cycling deterministically (rather than randomly) keeps each bucket's share
    even, so one address does not trip its limit while others sit idle.

    Args:
        pool: Number of distinct addresses, clamped to 131072; 0 disables the
            header entirely.
        issued: Index of this request within the phase.

    Returns:
        A header dict, or None when the pool is disabled.
    """
    if pool <= 0:
        return None
    # 198.18.0.0/15 is the RFC 2544 benchmarking range: reserved precisely for
    # this, and never a real client. It holds 131072 addresses, so the pool is
    # clamped rather than allowed to wrap onto itself and silently merge buckets.
    n = issued % min(pool, _FORWARDED_FOR_MAX)
    return {"X-Forwarded-For": f"198.{18 + (n >> 16)}.{(n >> 8) & 0xFF}.{n & 0xFF}"}


def _label(path: str) -> str:
    """Collapse parameterised paths so per-endpoint stats group correctly."""
    return "/tile/{z}/{x}/{y}" if path.startswith("/tile/") else path


@dataclass(frozen=True)
class Plan:
    """One phase of load: what to send, how fast, and for how long."""

    url: str
    scenario: str = "mixed"
    rate: float = 25.0
    duration: float = 30.0
    size: int = 0
    timeout: float = 30.0
    max_connections: int = 100
    seed: int = 0
    # Rate limits are keyed per client address, so a single-source run is capped
    # by the limiter long before the server saturates. >0 spreads requests over
    # that many synthetic X-Forwarded-For values, which the gateway honours only
    # when it was started with a matching --forwarded-allow-ips.
    forwarded_for_pool: int = 0
    # 0 randomises every payload, so nothing is ever served from cache. N > 0
    # cycles N fixed payloads, turning the run into cache-hit traffic once the
    # pool is warm -- the regime where gateway cost is the entire request.
    distinct_payloads: int = 0


async def generate(plan: Plan, out: list[Sample],
                   guard: Callable[[], bool] | None = None) -> bool:
    """Launch requests at a fixed arrival rate for the plan's duration.

    Args:
        plan: What to send and how fast.
        out: Collects one Sample per completed request.
        guard: Polled between launches; returning False stops the phase early
            and cancels what is still in flight. Used to abort before the
            server runs out of memory.

    Returns:
        True if the phase ran to completion, False if the guard stopped it.
    """
    rng = random.Random(plan.seed)
    payload = _payload_source(rng, plan)
    interval = 1.0 / plan.rate
    limits = httpx.Limits(max_connections=plan.max_connections)
    completed = True
    async with httpx.AsyncClient(base_url=plan.url, timeout=plan.timeout,
                                 limits=limits) as client:
        tasks: list[asyncio.Task[None]] = []
        start = time.perf_counter()
        issued = 0
        while time.perf_counter() - start < plan.duration:
            if guard is not None and not guard():
                completed = False
                break
            delay = (start + issued * interval) - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(
                _fire(client, payload(issued), out,
                      _forwarded_for(plan.forwarded_for_pool, issued))))
            issued += 1
        if completed:
            await asyncio.gather(*tasks)
        else:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return completed


async def _drive(args: argparse.Namespace, out: list[Sample]) -> None:
    """Run the CLI's single phase."""
    await generate(Plan(url=args.url, scenario=args.scenario, rate=args.rate,
                        duration=args.duration, size=args.size,
                        timeout=args.timeout,
                        max_connections=args.max_connections, seed=args.seed,
                        forwarded_for_pool=args.forwarded_for_pool,
                        distinct_payloads=args.distinct_payloads),
                   out)


def percentile(values: list[float], fraction: float) -> float:
    """Return the value at `fraction` of a sorted sample (nearest-rank)."""
    if not values:
        return 0.0
    index = min(len(values) - 1, round(fraction * (len(values) - 1)))
    return sorted(values)[index]


def error_rate(samples: list[Sample]) -> float:
    """Return the fraction of samples that were not 2xx."""
    if not samples:
        return 1.0
    ok = sum(1 for s in samples if 200 <= s.status < 300)
    return 1.0 - ok / len(samples)


def status_summary(samples: list[Sample]) -> str:
    """Return a compact "200=91, 429=3" style status breakdown."""
    counts: dict[int, int] = {}
    for sample in samples:
        counts[sample.status] = counts.get(sample.status, 0) + 1
    return ", ".join(f"{'transport-error' if k == 0 else k}={v}"
                     for k, v in sorted(counts.items()))


def _latency_line(latencies: list[float], indent: str = "  ") -> str:
    """Format a latency percentile line."""
    if not latencies:
        return f"{indent}latency    n/a"
    return (f"{indent}latency    p50={percentile(latencies, 0.50) * 1000:.0f}ms "
            f"p95={percentile(latencies, 0.95) * 1000:.0f}ms "
            f"p99={percentile(latencies, 0.99) * 1000:.0f}ms "
            f"max={max(latencies) * 1000:.0f}ms "
            f"mean={statistics.fmean(latencies) * 1000:.0f}ms")


def _print_per_endpoint(samples: list[Sample]) -> None:
    """Print one row per endpoint: count, latency percentiles, error rate."""
    paths = sorted({s.path for s in samples})
    if len(paths) < 2:
        return
    print(f"  {'endpoint':<20} {'n':>5} {'p50':>7} {'p95':>7} {'p99':>7} "
          f"{'err%':>6}  statuses")
    for path in paths:
        rows = [s for s in samples if s.path == path]
        latencies = [s.seconds for s in rows]
        print(f"  {path:<20} {len(rows):>5} "
              f"{percentile(latencies, 0.50) * 1000:>6.0f}m "
              f"{percentile(latencies, 0.95) * 1000:>6.0f}m "
              f"{percentile(latencies, 0.99) * 1000:>6.0f}m "
              f"{error_rate(rows) * 100:>5.1f}%  {status_summary(rows)}")


def _threshold_failures(args: argparse.Namespace, p95: float,
                        error_rate: float) -> list[str]:
    """Return the thresholds this run violated, if any were configured."""
    failures = []
    if args.max_p95 is not None and p95 > args.max_p95:
        failures.append(f"p95 {p95:.3f}s > {args.max_p95}s")
    if args.max_error_rate is not None and error_rate > args.max_error_rate:
        failures.append(f"error rate {error_rate:.3f} > {args.max_error_rate}")
    return failures


def _report(args: argparse.Namespace, samples: list[Sample], elapsed: float) -> int:
    """Print the summary and return the process exit code.

    Args:
        args: Parsed command-line arguments, including any thresholds.
        samples: Every completed request.
        elapsed: Wall-clock seconds the run took.

    Returns:
        0 when no configured threshold was exceeded, 1 otherwise.
    """
    latencies = [s.seconds for s in samples]
    failures = error_rate(samples)
    p95 = percentile(latencies, 0.95)

    print(f"\n{args.scenario} @ {args.rate}/s for {args.duration}s -> {args.url}")
    print(f"  seed       {args.seed} (pass --seed to replay this payload sequence)")
    if args.distinct_payloads > 0:
        print(f"  payloads   {args.distinct_payloads} distinct, cycled "
              f"(cache-hit traffic after the first {args.distinct_payloads})")
    print(f"  requests   {len(samples)} in {elapsed:.1f}s "
          f"({len(samples) / elapsed:.1f}/s completed)")
    print("  statuses   " + status_summary(samples))
    print(_latency_line(latencies))
    print(f"  errors     {failures * 100:.2f}%")
    _print_per_endpoint(samples)

    breaches = _threshold_failures(args, p95, failures)
    for breach in breaches:
        print(f"  THRESHOLD  {breach}")
    return 1 if breaches else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", choices=SCENARIOS, default="mixed")
    parser.add_argument("--rate", type=float, default=25.0,
                        help="requests launched per second (arrival rate)")
    parser.add_argument("--duration", type=float, default=30.0, help="seconds")
    parser.add_argument("--size", type=int, default=None,
                        help="waypoints, coordinates, stops or zoom, per scenario; "
                             "ignored by mixed, which uses each endpoint's default")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-connections", type=int, default=100)
    parser.add_argument("--forwarded-for-pool", type=int, default=0,
                        metavar="N",
                        help="spread load over N synthetic client addresses via "
                             "X-Forwarded-For, so the per-client rate limiter does "
                             "not cap the run; requires the gateway to trust this "
                             "source (--forwarded-allow-ips). 0 disables")
    parser.add_argument("--distinct-payloads", type=int, default=0, metavar="N",
                        help="cycle N fixed payloads instead of randomising each "
                             "one, so the run measures cache hits rather than the "
                             "engine. Small N warms in the first N requests; 0 "
                             "(default) never hits the cache")
    parser.add_argument("--seed", type=int, default=None,
                        help="fix the payload sequence; random each run by default, "
                             "because replaying a previous run's payloads measures "
                             "the cache instead of the service")
    parser.add_argument("--max-p95", type=float, default=None,
                        help="fail the run if p95 latency exceeds this many seconds")
    parser.add_argument("--max-error-rate", type=float, default=None,
                        help="fail the run if the non-2xx fraction exceeds this")
    args = parser.parse_args(argv)
    if args.size is None:
        args.size = DEFAULT_SIZE[args.scenario]
    if args.seed is None:
        args.seed = random.randrange(2**32)
    return args


def main() -> int:
    """Run the load test and return an exit code."""
    args = parse_args()
    samples: list[Sample] = []
    started = time.perf_counter()
    try:
        asyncio.run(_drive(args, samples))
    except KeyboardInterrupt:
        print("\ninterrupted; reporting what completed")
    return _report(args, samples, time.perf_counter() - started)


if __name__ == "__main__":
    sys.exit(main())
