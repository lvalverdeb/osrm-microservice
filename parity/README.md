# Differential parity harness

Replays a seeded request corpus against two gateways and diffs the responses,
so a rewrite can be judged on evidence rather than inspection.

## Running it

```bash
# Both gateways up, pointed at the same engine.
make parity

# The harness's own acceptance test: the Python gateway against itself,
# in-process, engine stubbed. Offline, so CI runs it as an ordinary test.
make parity-selfcheck
```

Exit codes distinguish two things that are easy to conflate: **1** means the
candidate diverged, **2** means the run was misconfigured — rate-limited,
half-down, unreachable. A harness that reports those identically stops being
believed.

## What is compared, and how

Tolerance is set per endpoint from what the implementations actually do, not
from a general notion of "close enough":

| Endpoint | Rule |
|---|---|
| `/matrix` | exact — observed byte-identical, so a delta is real signal |
| `/route`, `/trip`, `/match`, `/nearest`, `/matrix-graph` | float-tolerant to 1e-9° (~0.1 mm) |
| `/tile` | raw bytes |
| `/vrp`, `/vrp/allocate` | solution quality, not equality |
| `/health`, `/ready` | body shape; status mismatch is advisory, since each side probes the engine independently |
| `/metrics` | excluded by default — process-level and scrape-time series make a cross-diff noise |

The comparator walks **both** trees, so a key present on only one side fails as
loudly as a missing one. A numeric delta inside tolerance is still recorded, as
an advisory, so drift stays quantified instead of rounded into silence.

`/vrp` is judged on invariants per side — no stop served twice, capacity
respected, totals equal to the sum of their routes — plus the *distribution* of
distance ratios across the corpus. Per-case comparison is too weak to gate on: a
port that is systematically 2% worse passes on a lucky seed.

## Record and replay

`make parity-record` proxies a real engine and saves every response; point a
gateway's `OSRM_BASE_URL` at it and run the corpus. `make parity-replay` then
serves those fixtures, so later runs need no engine at all.

Two properties come free with replay, both invisible to a response diff:

- **Outgoing-request parity.** Replay answers only what it recorded. A gateway
  that builds a different upstream URL gets a 404 naming that URL, rather than a
  plausible answer. This is what `tests/test_parity_baseline.py` does for four
  endpoints by hand, extended to all of them.
- **Cache divergence.** The store counts fixture lookups, so two gateways that
  agree on every response but disagree on what they cache are distinguishable.
  Cross-comparison alone cannot see that — both can be wrong identically.

`tests/test_parity_replay.py` runs this offline against the committed fixtures,
which is how CI gets real coverage with no infrastructure.

## Two ways to get a meaningless pass

**Both sides failing identically compares equal.** Cases where both return 5xx
are marked `unproven` and surfaced separately; without that, a misconfigured run
reports a clean sweep.

**A shared warm cache tests nothing.** A response served from L2 never exercises
the gateway's upstream URL construction, so a run can pass while the two build
entirely different queries. Give each side its own `REDIS_URL` database, or
leave it empty on both. The `python` compose profile already uses database 1.

## What this cannot catch

Concurrency, in any form — it is sequential by construction, so races in the VRP
admission gate and cache stampedes are invisible. The rate limiter, which it
actively avoids tripping. Retry and backoff, which never engage against a
healthy engine. Anything outside the corpus. And the absence of divergence: the
1-ULP geometry difference was found by someone looking, not by a rule. The
both-trees walk is the main defence against the unknown, and it is worth more
than any tolerance tuning.
