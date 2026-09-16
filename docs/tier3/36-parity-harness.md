# 36 — The parity harness

*Tier 3: authored from `parity/`, `tests/test_parity_*.py`, the `parity-*` make
targets.*

A differential tester. It drives a seeded corpus of requests against **two**
gateway implementations and judges the results — the mechanism that made it
safe to replace a FastAPI service with a Rust one.

## Why it exists in this shape

The obvious way to check a port is to write assertions about the new
implementation. The harness instead compares two implementations over a corpus,
because the interesting defects in a port are the ones nobody thought to assert:
a dropped field, an extra field, a parameter spelled differently upstream, a
cache that fills differently.

## The pieces

| Module | Role |
|---|---|
| `corpus.py` | seeded request corpus, built from the load generator's payload factories |
| `client.py` | drives one gateway |
| `engine.py` / `upstream.py` | records and replays `osrm-routed` responses |
| `compare.py` | recursive response comparison with per-endpoint tolerance |
| `rules.py` | the tolerance for each endpoint, and why |
| `quality.py` | quality assertions for `/vrp`, where equality is the wrong question |
| `runner.py` | drives the corpus, judges each case, aggregates per endpoint |
| `report.py` | the output |

## The corpus is append-only by construction

Payload builders are **imported from `loadtest.run`** rather than copied, "so a
change to `build_route` moves the load corpus and the differential corpus
together and the two tools cannot silently disagree about what a request looks
like".

One behaviour is deliberately *not* inherited. The load generator draws every
payload from a single `Random(seed)`, so one endpoint's sequence depends on how
many requests the previous endpoint drew. Right for a mixed load run, wrong for
a golden corpus:

> raising `--cases` for `/route` would reshuffle every recorded `/vrp` case and
> invalidate the whole fixture set.

Each endpoint here gets its own stream derived from `(seed, endpoint)`, which
makes the corpus **append-only** — `cases=10` extends `cases=5` rather than
replacing it.

## The comparator is the load-bearing part

> One that is too lenient reports green on a broken port, and that failure is
> invisible from the outside.

So it is a **pure function over two decoded bodies**, unit-tested against
hand-written pairs rather than against a live gateway. Two decisions:

- **It walks both trees.** A key present on only one side is a failure.
  Missing-field and extra-field bugs are the most common porting defect, and a
  `for key in reference` walk misses half of them.
- **A numeric delta within tolerance is still recorded**, as an advisory, so
  drift stays quantified instead of being rounded away to silence.

## Tolerances come from measurement, not from a notion of "close enough"

| Endpoint | Rule | Why |
|---|---|---|
| `/matrix` | **exact** | observed byte-identical between the gateways, so any delta is real signal |
| `/route` and other pass-throughs | tolerance | both re-serialise the same upstream JSON, and float64 parse/format differences move the last ULP on some coordinates. The tolerance sits six orders of magnitude above that drift and six below any routing-relevant difference |
| `/tile` | **bytes** | raw protobuf |
| `/vrp`, `/vrp/allocate` | quality, not equality | clustering ties and float details legitimately differ |

## Quality assertions, where equality is the wrong question

Split in two, and the split is the insight:

**Per-side invariants**, checked against each response independently. They catch
what a cross-comparison is structurally blind to:

> a port that silently drops stops scores *better* on total distance, and two
> implementations that are wrong in the same way agree with each other
> perfectly.

**Cross-side quality**, comparing the two. Per-case distance comparison is too
weak to gate on — "a port that is systematically worse passes on a lucky seed" —
so the verdict is taken from the **distribution across the whole corpus**.

## Record and replay: the harness needs no engine

`upstream.py` stores real `osrm-routed` responses keyed by the request that
produced them. "Recording once turns *you need a routing engine* into *you
needed one once*", and it buys two things a response diff cannot:

**Outgoing-request parity, for free.** Replay only answers requests it has a
fixture for. If a gateway builds a different upstream URL or a different
parameter set, it gets a **miss rather than a plausible answer** — the same
property hand-written baseline tests gave for four endpoints, extended to all of
them with no assertions to maintain.

**Cache-divergence detection.** The store counts how often each fixture is
requested, so two implementations that agree on every response but disagree on
what they cache are distinguishable. "Cross-comparison alone is blind to that:
both can be wrong identically."

Fixtures live in `parity/fixtures/upstream/`, named by a digest of the request.

## Running it

```sh
make parity-selfcheck   # the harness's own tests: offline, no engine needed
make parity             # diff both gateways over the seeded corpus
make parity-record      # engine proxy that records upstream responses to fixtures
make parity-replay      # serve recorded fixtures instead of a real engine
```

`parity-selfcheck` is the one to run by default. It validates the comparator,
the corpus and the quality rules with no engine and no network. CI does not
invoke the target, but the same test files run inside `make test`.

## Its place in the suite

`tests/test_parity_*.py` are ordinary pytest files:

| File | Checks |
|---|---|
| `test_parity_compare.py` | the comparator, against hand-written pairs |
| `test_parity_corpus.py` | corpus determinism and stability |
| `test_parity_quality.py` | each quality assertion against a deliberately broken input |
| `test_parity_replay.py` | the recorded corpus against the gateway, with no engine |
| `test_parity_selfdiff.py` | the harness against itself |

`test_parity_quality.py` is worth copying as a pattern: every assertion is
checked against an input constructed to break it, so an assertion that can
never fail is caught.

These need the built binary and **skip locally, fail in CI** (doc 35).

## What it is for now

The FastAPI implementation it was built to compare against is gone. What
remains is durable: the recorded corpus is a **regression gate** — the current
gateway against its own recorded behaviour — and the replay engine is what lets
most of the test suite run with no routing engine at all.
