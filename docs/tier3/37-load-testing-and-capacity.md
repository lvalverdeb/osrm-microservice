# 37 — Load testing and capacity planning

*Tier 3: authored from `loadtest/run.py`, `loadtest/capacity.py`,
`benchmarks/BASELINE.md`, the `loadtest` / `capacity` / `spike-bench` targets.*

Three different measurements, often confused:

| Question | Tool |
|---|---|
| how does the **gateway** behave under a given arrival rate? | `loadtest.run` |
| what is the **safe operating envelope** of this deployed box? | `loadtest.capacity` |
| did a change make the **solver** produce worse plans? | `vrp.bench.runner`, doc 13 |

## The load generator

```sh
make loadtest LOADTEST_URL=http://127.0.0.1:8000 \
              LOADTEST_SCENARIO=mixed LOADTEST_RATE=25 LOADTEST_DURATION=30
```

### It is open-model, and that is the whole point

Requests are launched on a **fixed schedule** rather than after the previous one
returns, so a slow server shows up as rising latency instead of a quietly lower
request rate. That is the coordinated-omission trap "that closed-loop tools like
`ab` and `wrk` fall into": under those, a server that stalls simply receives
fewer requests, and the report says latency is fine.

### Payloads are randomised, which defeats the cache

Deliberately: "replaying one payload measures the cache, not the service".

But cache hits are a real workload too — **and the one where the gateway is the
whole request rather than a thin wrapper around the engine**. Use
`--distinct-payloads N` to draw from a small fixed set and measure that
regime instead. Both numbers are meaningful; they answer different questions.

### Scenarios

Every endpoint has one, and `mixed` fires a weighted blend concurrently, "which
is what a real client population looks like".

### Turning a run into a gate

```sh
... --max-p95 0.5 --max-error-rate 0.01
```

With thresholds the run exits non-zero, so it can be used as a check rather
than read as a report.

## The capacity assessment

```sh
make capacity LOADTEST_URL=http://127.0.0.1:8000
```

Four phases against a **live** server: endpoint smoke, leak check, arrival-rate
ramp, and payload ladders. It reports the safe operating envelope — the rate at
which latency breaks down, the largest VRP and matrix payloads the box
survives, and how much memory each costs.

Defaults, chosen to be informative in about three minutes:

| Phase | Values |
|---|---|
| rate ramp | 10, 25, 50, 100, 200 req/s |
| VRP payload ladder | 100, 500, 1,000, 2,000 stops |
| matrix payload ladder | 10, 50, 100 coordinates |

The VRP ladder tops out at `VRP_MAX_STOPS` (2,000) on purpose: past that the
gateway refuses with a 422 and there is nothing left to measure.

### The memory guard is the reason this tool exists

A probe polls the **server's** memory between request launches. When free
memory falls below the floor, the phase is **cancelled immediately**, so the
assessment "stops short of the OOM killer rather than discovering it".

> On a shared host the OOM killer picks by size and may take out a neighbouring
> jail, which is exactly what this guard exists to prevent.

The probe runs over SSH against the FreeBSD host, because the gateway's own
`/metrics` carries no RSS gauge there — the `process_*` collectors read
`/proc`, which FreeBSD does not mount by default (doc 17). Hence:

```sh
make capacity   # passes --ssh $(JAIL_HOST) --jail $(JAIL_NAME)
```

On Docker/Linux the `process_*` series exist and the SSH probe is unnecessary.

## Comparing two gateways

```sh
make spike-bench   # the same scenario against LOADTEST_URL and SPIKE_URL
```

Fires identical load at both and prints the two reports. For the comparison to
mean anything both must point at the **same** `osrm-routed`, and `WORKERS` must
match — "raising one without the other measures the configuration, not the
gateway".

## Which URL is which

`LOADTEST_URL` defaults to `http://127.0.0.1:8000`, the **jail's** port. Docker
publishes 8080, so pass it explicitly there:

```sh
make loadtest LOADTEST_URL=http://127.0.0.1:8080
```

## What the gateway's own numbers mean under load

Worth holding in mind while reading a report:

- **429s are not errors.** `hard_error_rate` excludes throttling, because a
  rate-limited request is the gateway working. Raise the `RATE_LIMIT_*`
  settings if you are trying to measure throughput rather than the limiter.
- **A 503 from `/vrp` is admission control**, not failure — and with
  `VRP_MAX_QUEUE_DEPTH` at its committed 0, an overloaded solve queue makes
  callers wait the full `VRP_QUEUE_TIMEOUT` (10 s) before refusing (doc 16).
  That shows up as a p95 pinned just above 10 s.
- **Cache state dominates the pass-through endpoints.** Compare like with like:
  a randomised run and a `--distinct-payloads` run are different experiments.
- **L1 and L2 hit series must not be summed** (doc 17).

## The solver baseline is a different instrument

`benchmarks/BASELINE.md` records solution quality, not latency, and states its
own limits plainly:

- The gate fails a change that makes the mean worse by more than **0.25
  percentage points**, or any single instance worse by more than **2.0%**.
- The budget is **iterations, not seconds** — "wall-clock varies with the
  machine, which would make the gate fail on a busy CI runner rather than on a
  real regression".
- The numbers are a **regression baseline, not a quality claim**. Gap against
  published best-known solutions needs the public-instance readers and a BKS
  registry; "until those land there is nothing here to compare against but
  ourselves".
- **Do not edit it by hand.** Re-record with `python -m vrp.bench.runner
  --record`; a hand-edited baseline "silently redefines what every later
  comparison means".

## Recorded measurements elsewhere in the system

Numbers already measured and worth not re-deriving:

| Measurement | Value | Source |
|---|---|---|
| upstream URL ceiling | answers to ~24,750 B, drops from ~25,000 | doc 15 |
| solve queue under overload | p50 10,008 ms against a 10 s timeout | doc 16 |
| snapping, 40 coordinates | 0.56 s serially vs 0.01 s batched | doc 19 |
| portfolio threads | 3.13× PyVRP, 1.00× pure-Python LNS | doc 20 |
| portfolio processes | 3.32× on that same LNS | doc 20 |
| 10,000² stored matrix | ~3.8 GB, ~12 minutes to build | doc 19 |
