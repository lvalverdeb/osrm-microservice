"""Assess a deployed gateway's capacity without running the host out of memory.

Runs four phases against a live server -- endpoint smoke, leak check, arrival
rate ramp, and payload ladders -- and reports the safe operating envelope: the
rate at which latency breaks down, the largest VRP and matrix payloads the box
survives, and how much memory each costs.

A memory probe polls the *server* between request launches. When free memory
falls below the floor the phase is cancelled immediately, so the assessment
stops short of the OOM killer rather than discovering it. On a shared host the
OOM killer picks by size and may take out a neighbouring jail, which is exactly
what this guard exists to prevent.

The probe runs over SSH against the FreeBSD host, since the gateway's own
/metrics carries no RSS gauge there (prometheus_client reads /proc, which
FreeBSD does not mount by default):

    uv run python -m loadtest.capacity --url http://10.211.55.33:8000 \
        --ssh developer@10.211.55.33 --jail api

Without --ssh the phases still run, but nothing guards memory, so the payload
ladders stop at their conservative defaults.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
import time
from dataclasses import dataclass, field

from loadtest.run import (
    BUILDERS,
    DEFAULT_SIZE,
    Plan,
    Sample,
    error_rate,
    generate,
    percentile,
)

# Phase defaults chosen to be informative in about three minutes.
RAMP_RATES = (10.0, 25.0, 50.0, 100.0, 200.0)
VRP_SIZES = (100, 500, 1000, 2000)
MATRIX_SIZES = (10, 50, 100)


@dataclass
class Memory:
    """One reading of server memory, in MB."""

    gateway_rss: int = 0
    available: int = 0


@dataclass
class PhaseResult:
    """What one phase of the assessment observed."""

    name: str
    samples: list[Sample] = field(default_factory=list)
    peak_rss: int = 0
    min_available: int = 0
    aborted: bool = False

    @property
    def p95_ms(self) -> float:
        """95th percentile latency in milliseconds."""
        return percentile([s.seconds for s in self.samples], 0.95) * 1000

    @property
    def hard_error_rate(self) -> float:
        """Fraction of requests that failed for reasons other than throttling.

        429 is the rate limiter doing its job -- shed load, not a failure.
        """
        countable = [s for s in self.samples if s.status != 429]
        return error_rate(countable) if countable else 0.0

    @property
    def throttled(self) -> int:
        """How many requests the rate limiter rejected."""
        return sum(1 for s in self.samples if s.status == 429)


class MemoryProbe:
    """Polls server memory over SSH, cheaply enough to guard a running phase."""

    def __init__(self, ssh_target: str, jail: str, floor_mb: int,
                 interval: float = 2.0) -> None:
        self._ssh = ssh_target
        self._jail = jail
        self._floor = floor_mb
        self._interval = interval
        self._last = Memory()
        self._checked = 0.0
        self.breached = False

    @property
    def enabled(self) -> bool:
        """True when a probe target was configured."""
        return bool(self._ssh)

    def _command(self) -> str:
        """Build the remote one-liner: gateway RSS, then the page counters."""
        jid_filter = f'jls -j {shlex.quote(self._jail)} jid' if self._jail else "echo 0"
        return (
            f'J=$({jid_filter} 2>/dev/null || echo 0); '
            "ps -axo jid,rss,command | "
            "awk -v j=$J '$1==j && /bin\\/uvicorn/ {s+=$2} END {print s+0}'; "
            "sysctl -n vm.stats.vm.v_free_count vm.stats.vm.v_inactive_count hw.pagesize"
        )

    async def sample(self) -> Memory:
        """Read memory once. Returns the previous reading if the probe fails."""
        if not self.enabled:
            return self._last
        process = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", self._ssh,
            self._command(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await process.communicate()
        values = out.decode().split()
        if len(values) < 4:
            return self._last
        rss_kb, free, inactive, page = (int(v) for v in values[:4])
        self._last = Memory(rss_kb // 1024, (free + inactive) * page // 1048576)
        return self._last

    async def watch(self, result: PhaseResult, stop: asyncio.Event) -> None:
        """Sample until `stop` is set, recording peaks and tripping the guard."""
        result.min_available = result.min_available or 10**9
        while not stop.is_set():
            memory = await self.sample()
            result.peak_rss = max(result.peak_rss, memory.gateway_rss)
            result.min_available = min(result.min_available, memory.available)
            if self.enabled and memory.available < self._floor:
                self.breached = True
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue

    def guard(self) -> bool:
        """False once the floor has been breached; passed to `generate`."""
        return not self.breached

    async def recover(self, timeout: float = 45.0) -> bool:
        """Wait for memory to climb back above the floor after an abort.

        A breach latches so the running phase stops, but later phases deserve a
        fair start: Python returns arenas lazily and the server needs a moment.

        Returns:
            True once memory is back above the floor plus a margin.
        """
        self.breached = False
        if not self.enabled:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            memory = await self.sample()
            if memory.available >= self._floor * 1.2:
                return True
            await asyncio.sleep(3.0)
        return False


async def run_phase(name: str, plan: Plan, probe: MemoryProbe) -> PhaseResult:
    """Run one load phase under the memory guard."""
    result = PhaseResult(name)
    stop = asyncio.Event()
    watcher = asyncio.create_task(probe.watch(result, stop))
    completed = await generate(plan, result.samples, guard=probe.guard)
    stop.set()
    await watcher
    result.aborted = not completed
    if result.min_available >= 10**9:
        result.min_available = 0
    return result


def _line(result: PhaseResult, label: str) -> str:
    """Format one result row."""
    flag = "  ABORTED (memory floor)" if result.aborted else ""
    memory = (f"rss={result.peak_rss}MB avail>={result.min_available}MB"
              if result.peak_rss else "")
    return (f"  {label:<22} n={len(result.samples):<5} "
            f"p95={result.p95_ms:>6.0f}ms err={result.hard_error_rate * 100:>5.1f}% "
            f"429={result.throttled:<5} {memory}{flag}")


async def phase_smoke(args: argparse.Namespace, probe: MemoryProbe) -> list[PhaseResult]:
    """Send a couple of requests to every endpoint; correctness before load."""
    print("\n[1/4] endpoint smoke -- every endpoint must answer 2xx")
    results = []
    for scenario in sorted(BUILDERS):
        plan = Plan(url=args.url, scenario=scenario, rate=2.0, duration=1.0,
                    size=DEFAULT_SIZE[scenario], timeout=args.timeout, seed=args.seed)
        result = await run_phase(scenario, plan, probe)
        results.append(result)
        print(_line(result, scenario))
    broken = [r.name for r in results if r.hard_error_rate > 0]
    print(f"  -> {'FAILED: ' + ', '.join(broken) if broken else 'all endpoints healthy'}")
    return results


async def phase_leak(args: argparse.Namespace, probe: MemoryProbe) -> PhaseResult:
    """Steady traffic, then compare RSS: growth here means a leak."""
    print(f"\n[2/4] leak check -- route @ 20/s for {args.leak_duration:.0f}s")
    before = (await probe.sample()).gateway_rss
    plan = Plan(url=args.url, scenario="route", rate=20.0,
                duration=args.leak_duration, timeout=args.timeout, seed=args.seed)
    result = await run_phase("leak", plan, probe)
    after = (await probe.sample()).gateway_rss
    print(_line(result, "route steady"))
    if probe.enabled:
        print(f"  -> RSS {before}MB -> {after}MB "
              f"({after - before:+d}MB; cache fill is expected, unbounded growth is not)")
    return result


async def phase_ramp(args: argparse.Namespace, probe: MemoryProbe) -> list[PhaseResult]:
    """Raise the arrival rate until latency or errors break, and report the knee."""
    print(f"\n[3/4] arrival-rate ramp -- mixed, {args.step_duration:.0f}s per step")
    results = []
    for rate in RAMP_RATES:
        plan = Plan(url=args.url, scenario="mixed", rate=rate,
                    duration=args.step_duration, timeout=args.timeout, seed=args.seed)
        result = await run_phase(f"mixed@{rate:g}/s", plan, probe)
        results.append(result)
        print(_line(result, f"{rate:g}/s"))
        if result.aborted or result.p95_ms > args.max_p95 * 1000 or result.hard_error_rate > 0.02:
            print(f"  -> knee at {rate:g}/s "
                  f"(p95 {result.p95_ms:.0f}ms, errors {result.hard_error_rate * 100:.1f}%)")
            break
    else:
        print(f"  -> no knee up to {RAMP_RATES[-1]:g}/s; raise the ramp to find it")
    return results


async def phase_payloads(args: argparse.Namespace, probe: MemoryProbe) -> list[PhaseResult]:
    """Scale payload size, which is what actually drives memory."""
    print("\n[4/4] payload ladders -- memory is driven by size, not request count")
    results = []
    for size in VRP_SIZES:
        plan = Plan(url=args.url, scenario="vrp", rate=1.0, duration=4.0,
                    size=size, timeout=args.timeout, seed=args.seed)
        result = await run_phase(f"vrp/{size}", plan, probe)
        results.append(result)
        print(_line(result, f"vrp {size} stops"))
        if result.aborted:
            print(f"  -> {size} stops is past this host's ceiling; "
                  "waiting for memory to recover")
            if not await probe.recover():
                print("  -> memory did not recover; stopping the assessment here")
                return results
            break
    for size in MATRIX_SIZES:
        plan = Plan(url=args.url, scenario="matrix", rate=4.0, duration=3.0,
                    size=size, timeout=args.timeout, seed=args.seed)
        result = await run_phase(f"matrix/{size}", plan, probe)
        results.append(result)
        print(_line(result, f"matrix {size} coords"))
        if result.aborted:
            await probe.recover()
            break
    return results


def _envelope(ramp: list[PhaseResult], payloads: list[PhaseResult],
              probe: MemoryProbe) -> None:
    """Print the safe operating envelope distilled from the phases."""
    print("\n=== operating envelope ===")
    good = [r for r in ramp if not r.aborted and r.hard_error_rate <= 0.02]
    if good:
        best = good[-1]
        print(f"  sustained rate   {best.name} at p95 {best.p95_ms:.0f}ms")
        share = best.throttled / len(best.samples) if best.samples else 0.0
        if share > 0.05:
            print(f"                   {share * 100:.0f}% of those were 429s -- beyond "
                  "this point you are measuring the rate limiter, not the server")
    vrp = [r for r in payloads if r.name.startswith("vrp/") and not r.aborted]
    if vrp:
        largest = vrp[-1]
        print(f"  largest VRP      {largest.name.split('/')[1]} stops "
              f"(peak RSS {largest.peak_rss}MB, p95 {largest.p95_ms:.0f}ms)")
    matrix = [r for r in payloads if r.name.startswith("matrix/") and not r.aborted]
    if matrix:
        print(f"  largest matrix   {matrix[-1].name.split('/')[1]} coordinates")
    if probe.breached:
        print("  NOTE             a phase hit the memory floor; the box is the limit")
    elif probe.enabled:
        floor = min((r.min_available for r in ramp + payloads if r.min_available), default=0)
        print(f"  memory headroom  {floor}MB free at the worst moment")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--ssh", default="",
                        help="SSH target of the host running the gateway; without "
                             "it there is no memory guard")
    parser.add_argument("--jail", default="api", help="jail name for the RSS probe")
    parser.add_argument("--floor-mb", type=int, default=250,
                        help="abort a phase when host free memory drops below this")
    parser.add_argument("--step-duration", type=float, default=10.0)
    parser.add_argument("--leak-duration", type=float, default=20.0)
    parser.add_argument("--max-p95", type=float, default=1.0,
                        help="ramp stops when p95 latency exceeds this many seconds")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args(argv)


async def _assess(args: argparse.Namespace) -> int:
    """Run every phase and print the envelope."""
    probe = MemoryProbe(args.ssh, args.jail, args.floor_mb)
    start = await probe.sample()
    print(f"capacity assessment -> {args.url}")
    if probe.enabled:
        print(f"  probe: {args.ssh} jail={args.jail} floor={args.floor_mb}MB | "
              f"gateway RSS {start.gateway_rss}MB, {start.available}MB available")
    else:
        print("  no --ssh probe: running unguarded, payload ladders stay conservative")

    smoke = await phase_smoke(args, probe)
    if any(r.hard_error_rate > 0 for r in smoke):
        print("\nendpoints are failing; fix that before measuring capacity")
        return 1
    await phase_leak(args, probe)
    ramp = await phase_ramp(args, probe)
    payloads = await phase_payloads(args, probe)
    _envelope(ramp, payloads, probe)
    return 0


def main() -> int:
    """Run the assessment."""
    args = parse_args()
    started = time.perf_counter()
    try:
        code = asyncio.run(_assess(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    print(f"\ncompleted in {time.perf_counter() - started:.0f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
