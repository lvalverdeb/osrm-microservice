# 20 — Solver architecture

*Tier 2: authored from `vrp/portfolio.py`, `vrp/solve/`, `vrp/lns.py`,
`vrp/localsearch.py`, `vrp/setpartition.py`, `vrp/polish.py`,
`vrp/decompose.py`.*

Not one algorithm. A portfolio of engines, a search layer, and two polishing
passes, with a single scoring authority above all of them.

```
            Problem (pinned matrix, frozen dataclasses)
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   PyVRP adapter        OR-Tools adapter      own LNS / local search
   (HGS, CVRPTW)        (escape hatch)        (SISR, ALG-3b)
        └─────────────────────┼─────────────────────┘
                              │  every incumbent, re-scored
                     canonical evaluator (vrp/objective.py)
                              │  cheapest legal plan
                     independent verifier (veto)
                              │
                    set-partitioning polish (ALG-6)
                    route-level exact polish (ALG-5)
```

## The portfolio, and the rule that makes it meaningful

Several engines run on the same `Problem`. The comparison problem this creates
is the reason `vrp/portfolio.py` exists, and it states the rule directly:
incumbents are scored by the canonical evaluator, **never by the engine's own
accounting**.

The argument is worth keeping: PyVRP counts one thing, OR-Tools another, the
project's own LNS a third. Comparing those numbers "picks whichever engine is
most generous to itself, which is not the same as picking the best plan". So
`objective_breakdown` is never read; every plan is re-scored from its routes on
one scale.

**The verifier has a veto.** Feasibility outranks optimality, so a cheap
illegal plan is not a better plan but a defect that happens to score well — and
a portfolio is exactly where that slips through, because a broken engine's plan
can be arbitrarily cheap.

An engine that raises is **rejected, not fatal**. The whole point of running
several is that one can fail, and an adapter that declines an instance — as the
OR-Tools one does for shipments — must not take the run down. `winner` is
`None` when no engine produced a legal plan; returning something regardless
"would be the portfolio inventing one".

### Determinism is engineered, not incidental

Results are collected in **engine order**, not completion order. Timing could
not change the winner anyway (it is a `min` over a name-keyed dict with a name
tiebreak), but completion order would change the *report*: `scores` and
`rejected` would list engines differently on every run, and two runs agreeing
about every number would serialise to different bytes.

### Parallelism is opt-in, and the numbers are recorded

`workers=1` is the default because "a library that parallelised unasked would
make every existing caller's run non-reproducible" — single-threaded and
iteration-limited is the reproducible mode used for all regression tests.

| Executor | Speedup on PyVRP | Speedup on the pure-Python LNS |
|---|---|---|
| `thread` (default) | 3.13× | 1.00× |
| `process` | — | 3.32× |

Threads give separate cores only to engines that release the GIL. Processes
give them to everything, at the cost of picklability: an engine that is a
lambda or closure raises `UnsendableEngine` **before the pool starts**, so the
error names the member rather than arriving as `BrokenProcessPool` from three
frames inside the standard library.

`workers=0` raises rather than being clamped: "nought workers is not 'no
parallelism': it is a pool that runs nothing".

### Win rates make the premise checkable

`instance_signature` labels an instance by coarse **bands**, not exact counts —
"a signature distinguishing 41 stops from 42 would give every instance its own
bucket, and a bucket with one observation tells nobody anything". `WinRates`
then records which engine won which shape. The claim that different engines
suit different shapes is only actionable if somebody records where.

Zero observations report a rate of zero, with `observations` available to
distinguish "loses here" from "never offered one".

## The adapters

Each adapter compiles a `Problem` into an engine's model and maps the result
back. Two rules are common to both:

**Travel comes from the pinned matrix, never from coordinates.** PyVRP will
compute Euclidean distances from `x`/`y` if allowed, "and silently disagreeing
with the matrix the plan is later verified against is exactly the drift INV-4
exists to catch". Coordinates are passed for display only.

**The engine's own arrival and service times are carried back**, not recomputed
in the adapter. That is deliberate: the independent verifier then checks *the
solver's* arithmetic against the matrix, instead of checking our evaluator
against itself.

One compilation detail shows how a constraint survives translation: PyVRP
declines an order by forgoing its prize, and a prize is one number, but FR-13
wants lexicographic tier protection. `tier_bonuses` derives bonuses **from the
instance** such that a tier's bonus strictly exceeds everything obtainable from
every tier beneath it — never a constant, for the same reason the objective
scales are instance-derived (doc 22).

## The search layer

### Local search (ALG-2)

Three accelerations, one of which the specification insists on before any
tuning work: O(1) move evaluation.

| | naive | accelerated |
|---|---|---|
| candidates | every pair | k nearest eligible neighbours |
| cost of a move | recompute the whole route | delta from the four affected edges |
| sweeps | every node, every time | skipped while the don't-look bit is set |

Both are implemented, because **the claim is comparative and a benchmark
against an absent baseline is not a benchmark**. The naive version "is not a
straw man: it is what a competent person writes first".

**Scope is stated honestly.** The full move set is relocate(1..3), swap(1..3),
2-opt, 2-opt\*, or-opt, swap\*, and pickup-delivery pair moves. Implemented are
relocate(1) and 2-opt on a single route — enough to measure throughput, which
is a property of the acceleration machinery rather than of the move catalogue,
and "not enough to be the production search".

### Ruin-and-recreate (ALG-3b, SISR)

Three pieces, each with a plausible implementation that does nothing — which is
why each is tested against its own degenerate form:

- **Adjacent string removal**: short contiguous runs of visits, near one
  another in space, across several routes. "A removal that took scattered nodes
  would be random removal wearing this one's name."
- **Greedy insertion with blinks**: each candidate position skipped with small
  probability. "Blinks at probability zero are plain greedy; the parameter has
  to change the outcome or it is decoration." At `blink=0` it is deterministic,
  which the tests rely on.
- **Simulated annealing acceptance**: geometric temperature decay. "An annealer
  that never accepts a worse solution is hill-climbing with extra arithmetic."

`random_ruin` exists so the comparison is against something real rather than a
description of something. An unknown ruin operator raises rather than
defaulting to SISR — "a typo that quietly ran the wrong operator would make
every comparison between them meaningless".

Budgets are **iteration counts, never wall-clock** (CON-4).

## Polishing

### Set partitioning over the route pool (ALG-6)

The premise: a search discards good work. Run A finds an excellent northern
route and a mediocre southern one; run B the reverse. The best plan is already
in the union of what they built, and nobody has assembled it.

Three things this implementation is careful about:

- **Exactly once, not at least once.** Relaxing coverage to `>=` turns this
  into set covering: easier, cheaper-looking, and it produces plans that
  deliver the same parcel twice. One character in the model, so it has its own
  test.
- **Columns are individually verified** before admission. A rejected route is
  not an error — the pool is fed by several engines, and one of them proposing
  an impossible route must not contaminate the model.
- **Column cost is read from the matrix**, not from whatever produced it, with
  particular force here: the columns come from several engines each counting
  something slightly different.
- **No partition means `None`**, not a best-effort partial cover: "a partial
  cover is not a cheap plan, it is undelivered freight."

The pool is an acknowledged approximation. ALG-6 wants every route the search
ever touched; PyVRP does not expose intermediate solutions, so the pool is fed
by repeated short runs at different seeds. `build_pool` says which it is doing,
and this is the part to revisit if the recovered percentage needs to be larger.

The polish **selects** routes and does not re-sequence them, because the cost
the model minimised is the cost of those sequences and no others.

### Route-level exact polish (ALG-5)

Per-route passes after the metaheuristic spends its budget, including departure
scheduling: shifting departure and distributing waiting to minimise duty
duration and lateness, respecting driving-hours rules. Exactly solvable per
route and nearly free.

## Above the monolithic regime

Above roughly 2,000–3,000 stops, monolithic search degrades. `vrp/decompose.py`
splits the instance; `PlanarMatrix` (doc 19) removes the stored-matrix
requirement; `vrp/accelerate.py` describes an optional GPU profile. The target
is 10,000 stops within an hour.

## What the code does not claim

- The local search is a **measurement**, not the production search. If it ever
  became one, the module says it is the first thing in the project with a real
  argument for Rust — "it is a tight numeric loop and nothing else here is".
- Staged optimisation for very large instances (the answer to objective
  overflow above 10,000 stops) is **not implemented**; see doc 22.
- The route pool is smaller than the technique assumes, and says so.
