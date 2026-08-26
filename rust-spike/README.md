# Rust gateway spike

`/route` and `/matrix` reimplemented in axum, existing for one purpose: to
replace an inference about the gateway's cost with a measurement. It is not a
port and not a deployment candidate.

> **The port this motivated now exists, and has been measured on the jail.**
> Everything below was measured on a laptop against a constant-time stub, which
> is the right way to isolate gateway cost and the wrong way to predict what a
> deployment will do. On real hardware, with both complete gateways on the same
> jail against the same engine, ordinary traffic showed **no meaningful latency
> difference** — the engine is the constraint, exactly as
> [the scaling plan](../docs/planning/SCALING_READINESS_PLAN.md) predicted. The
> port pays on cache hits (~2×) and memory (~3.8×), not on throughput. Cite
> those numbers, not the 3× below.
>
> The figures here remain valid for what they measure: this two-endpoint spike,
> against a stub, with none of the features either real gateway carries.

## Running it

```sh
cargo build --release
OSRM_BASE_URL=http://127.0.0.1:5000 HOST=127.0.0.1 PORT=8001 WORKERS=1 \
    ./target/release/osrm-gateway-spike
```

Settings mirror `gateway/src/config.rs` by name: `OSRM_BASE_URL`, `HOST`, `PORT`,
`WORKERS`, `L1_CACHE_TTL`, `L1_CACHE_MAXSIZE`, `OSRM_CLIENT_TIMEOUT`,
`MATRIX_MAX_CELLS`, `HEALTH_CHECK_COORDS`, `HEALTH_CHECK_TIMEOUT`. `WORKERS`
defaults to 1 so a run matches a single uvicorn worker; raise it only alongside
the Python side. `HOST` defaults to loopback; both deployment paths set
`0.0.0.0` explicitly.

A `.env` in the working directory is read first, the same file and the same
precedence the Python gateway uses: later duplicate keys win, and a real
environment variable beats the file. Point elsewhere with `SPIKE_ENV_FILE`.

### Deployed alongside the Python gateway

Both deployment paths can run the spike beside the real gateway against the same
engine — that is how to reproduce the table below on the 2-core jail rather than
a laptop. See [deployment.md](../docs/deployment.md#optional--the-rust-evaluation-spike).

```sh
make compose-spike-up      # Docker, behind a compose profile
make jail-spike-up         # FreeBSD jail, needs `make jail-bootstrap` first
make spike-bench           # same scenario against both, back to back
```

`/health` and `/ready` behave as they do in the Python gateway: `/health` always
answers 200 with a `degraded` body so a dashboard can read the detail, `/ready`
answers 503 when the engine is down so a balancer drains the node. Both
deployment health checks probe `/ready`.

Because it speaks the same request contract, `loadtest/run.py` drives either
gateway:

```sh
uv run python loadtest/run.py --url http://127.0.0.1:8001 --scenario route --rate 200 --duration 20
```

## Method

Both gateways ran against a **constant-time stub** standing in for
`osrm-routed` (`scratchpad/stub_osrm.py`: pre-serialised 20 KB route and 151 KB
table payloads, 4 uvicorn workers so the stub never became the constraint).
Removing the engine is deliberate — it is the variable the comparison is trying
to hold still, and it makes the gateway the only thing being measured.

One worker each. Python ran with its rate limits raised to 1,000,000/minute: at
200 req/s the deployed 600/minute limit turned 85% of the run into 429s, which
would have measured the limiter rather than the gateway.

Development host (macOS, Apple silicon), 2026-08-21. **Not** the 2-core FreeBSD
jail — absolute numbers there will be lower on both sides.

## Results

`route`, 20 KB responses, one worker:

| Offered | Python completed | Python latency | Rust completed | Rust latency |
|---|---|---|---|---|
| 200/s | 199.4/s | p50 3 ms, p95 4 ms, p99 20 ms, max 59 ms | 199.6/s | p50 2 ms, p95 3 ms, p99 3 ms, max 10 ms |
| 400/s | **253/s** (saturated) | p50 532 ms, p95 11.6 s | 398/s | p50 1 ms, p95 2 ms |
| 800/s | **141/s** (collapsed) | p50 43 s, max 84 s | 797/s | p50 1 ms, p95 1 ms |

`matrix` 100x100, 151 KB responses:

| Offered | Python latency | Rust latency |
|---|---|---|
| 30/s | p50 8 ms, p95 12 ms, p99 32 ms | p50 5 ms, p95 8 ms, p99 10 ms |
| 100/s | p50 5 ms, p95 8 ms, p99 121 ms | p50 3 ms, p95 4 ms, p99 7 ms |

Peak RSS sampled during a `matrix` run at 60/s, identical 1024-entry caches:
**Python 660 MB, Rust 321 MB**. Sample during load, never after — the macOS
memory compressor reclaims idle pages and a quiet process reads far lower than
it ever ran at.

## What this does and does not show

**Throughput ceiling per worker is roughly 3x apart**: Python saturates near
250-280 req/s, the spike was still flat at 797 req/s and its ceiling was never
found. **Latency below saturation differs by 1-2 ms**, which matches the
gateway-only figure measured separately (0.34 ms for `/route` through
`ASGITransport`, before uvicorn's socket handling).

Which of those two numbers matters depends entirely on what the bottleneck is,
and today it is not the gateway. The jail measured ~150 req/s uncached against
`osrm-routed` on 2 cores — below even Python's ceiling. A faster gateway in
front of that engine queues on the same engine.

That held up. When the full port was measured on the jail on 2026-08-24, mixed
traffic at 30/s came out p50 3 ms for Rust against 4 ms for Python, with
Python's *mean* marginally lower — no meaningful difference, and none of the 3×
ceiling reachable. The throughput headroom below is real and, at this scale,
unspendable. The measurement, and the two places the port does pay, are recorded
under "Not in this plan" in
[the scaling readiness plan](../docs/planning/SCALING_READINESS_PLAN.md).

The places where the gap would show up are narrower than the table suggests:

- **Cache-hit traffic**, where the gateway is the entire request. The load test
  measured 1-9 ms cached against 82 ms uncached, a 10-40x multiplier, so hits
  can dominate a real workload — and every one of them is pure gateway cost.
  The tables above are *all* cache misses: `loadtest/run.py` randomises every
  payload by design. Drive that regime with `--distinct-payloads N`, which
  cycles N fixed payloads instead, against either gateway.
- **CPU contention on a shared box**, where gateway and engine compete for the
  same 2 cores. At 150 req/s and ~3 ms of CPU per request, the Python gateway
  spends roughly half a core; the spike would spend a fraction of that, and the
  engine gets the difference.
- **Tail latency**, where p99 was 20 ms against 3 ms at a load both handled
  comfortably.

## Why the comparison flatters the spike

Stated plainly, because none of these are free to add back:

- **No rate limiting, metrics, or tracing.** The Python gateway ran with
  slowapi, the Prometheus middleware, and OpenTelemetry instrumentation active —
  per-request work the spike never does. Some of the gap is those features, not
  the language.
- **No Redis L2 tier, no retry/backoff.**
- **No per-coordinate routing options** (`bearings`, `radiuses`, `hints`,
  `approaches`), which the Python models validate on every request.
- **Two endpoints of eleven**, and not the hard one. The VRP solver would need
  `ndarray` and `petgraph` in place of numpy and networkx, and that is where a
  real port stops being mechanical.
- **The stub answers instantly.** A real 80 ms engine caps in-flight
  concurrency and would hide much of the difference.

## Fidelity note

`/matrix` responses are byte-identical between the two. `/route` responses
differ in the last ULP on ~13% of geometry coordinates (`10.050849173924503`
vs `...504`) — a float64 parse/format difference of ~1e-15 degrees, or about
1e-10 m. Harmless for routing, but a port would not be byte-for-byte.
