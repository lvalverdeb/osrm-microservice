# 38 — Scaling limits and tuning

*Tier 3: authored from `gateway/src/config.rs`, `deploy/env/app.env`,
`vrp/decompose.py`, `vrp/accelerate.py`, `vrp/matrix.py`.*

Every limit here is a refusal, not a truncation. The system says no and names
the number rather than quietly serving a smaller answer — which is the property
that makes these safe to tune.

## The hard limits

| Limit | Setting | Committed | Crossing it |
|---|---|---|---|
| stops per solve | `VRP_MAX_STOPS` | 2,000 | 422 |
| matrix cells per request | `MATRIX_MAX_CELLS` | 10,000 | 422 |
| coordinates per `/nearest/batch` | `NEAREST_MAX_COORDINATES` | 1,000 | 422 |
| upstream URL bytes | `OSRM_MAX_URL_BYTES` | 24,000 | 422 naming both figures |

`OSRM_MAX_URL_BYTES` is the one most likely to need changing, and only
downwards: `osrm-routed` itself answers to ~24,750 bytes, but **nginx and
Apache cap a request line near 8 KB**. A proxy in front of the engine makes
24,000 wrong.

## The concurrency knobs

| Setting | Committed | Governs |
|---|---|---|
| `WORKERS` | 1 | tokio worker threads in the one process |
| `VRP_MAX_CONCURRENCY` | 1 | solves running at once |
| `VRP_QUEUE_TIMEOUT` | 10.0 s | how long a request waits for a slot |
| `VRP_MAX_QUEUE_DEPTH` | **0 (disabled)** | how many may wait |
| `VRP_CHUNK_CONCURRENCY` | 4 | concurrent engine calls per solve |
| `NEAREST_BATCH_CONCURRENCY` | 8 | concurrent engine calls per batch |
| `MATRIX_BATCH_SIZE` | 500 | coordinates per upstream `/table` |
| `VRP_CHUNK_SIZE` | 80 | stops per vehicle chunk |

Two principles behind the fan-out limits:

**Peak memory is stops × concurrent solves**, which is why
`VRP_MAX_CONCURRENCY` defaults to 1 on a 2 GB jail.

**`osrm-routed` is shared**, so `VRP_CHUNK_CONCURRENCY` and
`NEAREST_BATCH_CONCURRENCY` exist to stop one caller taking all of it.

### Tuning order for more throughput

1. **Raise `WORKERS`** first. Threads in one process, one metrics registry, no
   multiprocess machinery — the cheapest change. Both deployments ship 1.
2. **Raise `VRP_MAX_CONCURRENCY`** only with memory headroom, and measure with
   `make capacity`, which aborts before the OOM killer (doc 37).
3. **Set `VRP_MAX_QUEUE_DEPTH`** to a small multiple of the concurrency. At its
   committed 0 the depth bound is off, so overloaded callers pay the full
   10-second timeout before being refused — the measured p50 was 10,008 ms
   (doc 16).
4. **Give Redis to every instance.** Without `REDIS_URL` the rate limiter
   counts in process, so N instances behind a balancer allow N times the
   configured limit.
5. **Raise the cache tiers** (`L1_CACHE_MAXSIZE` 1024, `L1_CACHE_TTL` /
   `REDIS_TTL` 900 s) if the workload has repeat traffic. Randomised load
   defeats them by construction, so measure with `--distinct-payloads`.

## Where the routing platform's scale ceilings are

| Stops | Regime | Mechanism |
|---|---|---|
| ≲ 2,000–3,000 | monolithic | stored matrix, single search |
| 2,000–10,000 | decomposed | `vrp/decompose.py` + `PlanarMatrix` |
| 10,000 | target | within one hour |

### Why a stored matrix stops working

10,000² is 100 million cells — measured at **~3.8 GB and roughly twelve minutes
just to build**, "a fifth of the budget spent before any search starts". Above
that, `PlanarMatrix` computes straight-line travel on demand at a fixed 30 km/h,
and a test pins it cell-for-cell against a stored matrix so the two cannot
drift.

Below that ceiling, tiling plus the pair cache is what keeps a large matrix
affordable (doc 19).

### Decomposition is an orchestrator, not a for-loop

Partition, re-optimise sub-problems against an incumbent, repair the seams —
and **never score the result by adding up what the sub-solvers said about their
own pieces**.

The reason it cannot be a loop is that some constraints are global:

> depot inventory, dock capacity and shared-vehicle constraints MUST be enforced
> globally, never per cluster.

> Every sub-problem can be individually perfect and the concatenation still
> fiction — fifty clusters each politely sending one vehicle to the same dock at
> 06:00, none aware of the other forty-nine.

Or, as the requirement puts it: "if 40 vehicles are planned to depart at 06:00
and there are 8 bays, the plan is fiction". No sub-solver can see that, so the
orchestrator owns a global scheduling step and INV-12 judges it.

The partitioner is **adaptive** because fixed partitioning is explicitly
insufficient.

## Parallelism inside a solve

`run_portfolio(workers=N, executor="thread"|"process")`:

| Executor | PyVRP | pure-Python LNS |
|---|---|---|
| thread | 3.13× | 1.00× |
| process | — | 3.32× |

Threads give separate cores only to engines that release the GIL. Processes
give them to everything, at the cost of picklability — a lambda or closure
raises `UnsendableEngine` before the pool starts.

`workers=1` is the default and is the reproducible mode. Raising it is a
deliberate trade of replayability for wall-clock (doc 29).

## GPU: an accelerator profile, never a dependency

> The optimisation core MUST run on commodity CPU. GPU acceleration MAY be an
> optional accelerator profile, never a hard dependency.

That is a **negative** requirement, and `vrp/accelerate.py` exists to make the
absence checkable: nothing in the solve path reaches for a GPU library, and
"the machine that demonstrates it best is one with no GPU — which is where the
tests run".

Two design rules follow:

- **The flag is a parameter, not an environment variable.** Nothing in `vrp/`
  reads the environment; the library is configured by its caller. "An
  accelerator that switched itself on because of an ambient variable would also
  be one that switched itself on in a test run", and replayability would become
  a matter of what was exported that day.
- **The import is not attempted while the profile is off**, so an absent
  library costs nothing.

## Sizing the box

Recorded constraints, not recommendations:

- The FreeBSD jail is **2 cores / 2 GB, shared with two other jails**. That is
  why the gateway builds under a thin-LTO cargo profile, why
  `VRP_MAX_CONCURRENCY` is 1, and why the capacity tool has a memory floor.
- The gateway image is a slim Debian runtime with one static-ish binary and
  `curl`; the OSRM images carry the built graphs, which dominate disk.
- Three graphs are built per extract, one per profile.

## What is not tunable

- **Rate-limit windows are fixed**, not rolling — inherited from slowapi's
  `limits` library. A client can send double the limit across a window boundary.
- **The 429 carries no `Retry-After`**; the 503 from admission control does.
- **Retry exhaustion surfaces as 500**, not the engine's status (doc 15).
- **Objective scale factors are derived from the instance** and cannot be
  configured; that is what keeps tiers lexicographic (doc 22).
