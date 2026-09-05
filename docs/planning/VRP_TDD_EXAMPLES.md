# VRP SDD — example-driven implementation plan

One runnable example per unit of functionality in
[`../vrp-spec-driven-development.md`](../vrp-spec-driven-development.md), ordered
so each can be written **before** the code that satisfies it.

Companion to [VRP_SDD_FIT_GAP.md](VRP_SDD_FIT_GAP.md), which measures how far
today's implementation is from the specification. This document is the route
from one to the other.

**Status — 61 of 64 examples done**, verified against the repository on
2026-09-02 (912 tests passing, CI green on `2928a30`). `E-40` moved from
Blocked to Done when `T-40`'s blocker turned out to be a misreading, and `E-80`
was added for the integration that genuinely does wait on data. Two of them do not have the file §11a first named for them, and the
rows say where they went instead rather than claiming a path that does not
exist. Every row below is marked
`**Done.**`, `**Blocked**` or `**Not built**` in its *Passes when* column.

The authority on task status is the backlog in
[§13 of the SDD](../vrp-spec-driven-development.md), which carries the same date
and the same counts. This document tracks *examples*, and an example can exist
before or after the task it belongs to, so the two are kept in step by hand
rather than derived from one another.

The matching totals are a coincidence of two offsetting gaps rather than a 1:1
mapping, and it is worth knowing which: `T-01` (repository scaffold) has no
example row, because there is nothing to demonstrate about a directory layout
that a passing CI run does not already show; and `E-03b` belongs to §11.2
rather than to any single task, since evaluator-verifier agreement is a property
of the pair. Counting rows as tasks would therefore be wrong twice and right by
accident.

That is worth saying because they fell out of step badly. These markers stopped
being maintained around `E-13`: fourteen rows said **Done.** while fifty tasks
were finished, so the document understated progress by more than half and there
was no way to tell from reading it. A marker nobody can date is a marker nobody
can trust, which is why this note carries a date and a commit rather than a
tick. Where a row's **Done.** has no summary beside it, the commit for that task
is the record -- the earlier rows carry a paragraph because they were written as
the work happened, and backfilling thirty-five of those from memory would
produce prose less reliable than the git history it paraphrased.

---

## 1. How to use this

The SDD already defines its own backlog (§13, `T-01`…`T-67`) and a seven-level
test taxonomy (§11.1). This plan does not invent a competing order — it attaches
one executable example to each task, so the backlog becomes a red-green
sequence.

The loop per row:

1. **Write the example.** It fails, because nothing implements it yet.
2. **Make it pass**, narrowly.
3. **Keep it.** It is now a regression test and the documentation for that
   feature, which is why the example is worth writing even when a plain unit
   test would prove the same thing.

### Where each lives

| Kind | Location | Run by |
|---|---|---|
| Acceptance example — the executable spec | `tests/vrp/` | `make test`, CI |
| Demonstration — human-facing, prints and plots | `examples/src/fleet/` | `make examples` |

Most rows below need both: a test that asserts the behaviour and a demonstration
that shows it. Where one file can honestly do both, prefer one.

### Placement: Rust gateway or Python VRP package?

**Every item carries a placement recommendation, made when the item is tackled
and recorded with it.** Two homes exist and the choice is not obvious from the
requirement alone:

| Home | What belongs there |
|---|---|
| `gateway/` (Rust) | Anything on the per-request hot path, anything that is part of the OSRM contract, and anything that must work with no Python present |
| `vrp/` (Python) | Optimisation logic, constraint modelling, calibration, and anything whose value comes from the PyVRP / OR-Tools / pandas ecosystem |

Ask in this order:

1. **Does a request wait on it?** Matrix construction, caching, retry, rate
   limiting, admission control and relaying are gateway work. A 2,000-stop
   solve is not — it is already shed and queued rather than served inline.
2. **Does it need the Python ecosystem?** If the honest implementation is "call
   PyVRP", it is Python. SDD §7.4 forbids a bespoke metaheuristic without an
   ADR, so "write it in Rust instead" is rarely the cheap answer it looks like.
3. **How often will it change?** Constraint semantics and objective tuning move
   constantly and belong where iteration is cheap. Wire formats and transport
   behaviour move rarely and belong where they are fast.
4. **Who must be able to run it?** The independent verifier has to be callable
   from CI, from a notebook, and from the gateway before a plan is published
   (§11.2). That argues for Python logic with a thin gateway endpoint over it —
   the split already used for `/verify` in `T-66`.

Record the recommendation and its reason in the item's commit, even when the
answer seems obvious. The cases that went wrong in this repository were the ones
nobody argued about: the rate limiter was rewritten in Rust without a Redis path
and silently undid a verified P0 fix, and `vehicle_count` sits in the gateway
schema doing nothing because nobody asked which side owned fleet sizing.

### Test levels

Taken from SDD §11.1, and cited per row:

| Level | Meaning |
|---|---|
| L1 | Unit — evaluators, rule engines, adapters |
| L2 | Property — generated instances against invariants INV-1…INV-9 |
| L3 | Golden — frozen instance + seed, byte-identical solution (CON-4) |
| L4 | Benchmark — public sets against BKS thresholds |
| L5 | Corpus — frozen production instances, no regression > 0.5% |
| L6 | Shadow — live traffic, plan produced but not executed |
| L7 | Canary — small production subset |

### Named problem classes

SDD §3.4 maps the five classes the literature and the benchmark sets speak in —
TSP, CVRP, VRPTW, MDHVRPTW, PDPTW — onto the requirements that compose them, and
names the task that delivers each. The **Variant** column is the reverse lookup:
it carries §3.4's mapping down to the example that proves the class works.

It is populated **only** where §3.4 says so, so the two documents cannot drift —
a class appears here if and only if §3.4 lists its task. A blank cell therefore
means "§3.4 names no class for this task", not "unclassified": most rows are a
constraint family that several classes share, and inventing a variant for them
would be a claim §3.4 does not make.

Six rows carry a value:

| Variant | Example | Why that row |
|---|---|---|
| TSP | `E-39` | Already served in production via OSRM `/trip`; `E-39` is the departure-time polish SDD §7.5 keeps for it |
| CVRP | `E-12` | The adapter is the first thing that solves capacity + fleet + depot end to end |
| VRPTW | `E-12`, `E-23` | `E-12` for the single window, `E-23` for disjoint, soft and released ones |
| MDHVRPTW | `E-21` | Per-vehicle capacity and cost, multiple depots, distinct start and end — §3.4 calls this the shape this business actually has |
| PDPTW | `E-20`, `E-13` | `E-20` for precedence and non-monotonic load, `E-13` for the objective that scores it |

---

## 2. The oracle comes first

Everything downstream depends on being able to answer *"is this plan even
legal?"* independently of whatever produced it. That is `T-04`, and the SDD
marks it **[GATE]** for good reason: a solver graded by its own evaluator will
happily agree with itself.

So the first three examples are not about routing at all:

| # | Example | Task | Level | Passes when |
|---|---|---|---|---|
| **E-01** | `tests/vrp/test_domain_model.py` | `T-02` | L1 | **Done.** Every entity in §4 round-trips through validation; integer units throughout; out-of-range values rejected with the field named |
| **E-02** | `tests/vrp/test_canonical_evaluator.py` | `T-03` | L1 | **Done.** Objective and timeline recomputed from a solution match hand-worked fixtures to the unit; deterministic across runs |
| **E-03** | `tests/vrp/test_independent_verifier.py` | `T-04` **[GATE]** | L1+L2 | **Done.** INV-1…INV-6 and INV-9 each have a violating fixture that is caught and a legal one that passes; INV-7 and INV-8 report *not applicable* rather than passing. The no-solver-imports rule is enforced by reading the module's imports |

A fourth landed alongside them, not in the original plan:

| # | Example | Covers | Level | Passes when |
|---|---|---|---|---|
| **E-03b** | `tests/vrp/test_evaluator_verifier_agreement.py` | §11.2 | L2 | **Done.** 300 generated instances: every timeline the evaluator builds satisfies the verifier, and the two recompute the same distance |

§11.2 calls a discrepancy between evaluator and verifier a P1 defect, which is
only a meaningful claim if something compares them. This is also the cheapest
approximation of the L2 property level until the real generator arrives in
`T-05`.

**Implementation lives in `vrp/`**: `model.py`, `evaluator.py`, and
`verify/verifier.py` in its own package, importing neither.

A demonstration landed with them, `examples/src/fleet/verify_delivery_plan.py`:
it takes a slice of the Costa Rica dataset, fetches a real road matrix through
the gateway, builds a plan by nearest-neighbour, evaluates it, and has the
verifier judge it — then reports the same plan with its distance understated by
a kilometre and shows INV-9 catching that. A verifier only means something
against a plan it did not produce.

> **Resolved.** The examples folder was `examples/src/vrp/`, which shadowed the
> root `vrp/` package: a script inside it saw the *folder* as a namespace
> package, so `import vrp` succeeded and gave the wrong thing. It is
> `examples/src/fleet/` now, and the repository root is put on `sys.path` once
> in `examples/src/config.py` — the module every example already imports —
> rather than by a shim repeated in each script.

**E-03 is the highest-value example in this document.** It is cheap, depends on
nothing, and every later row is judged by it. Write it first even if the rest of
the plan is deferred.

---

## 3. Slice 0 — Foundations

| # | Example | Task | Satisfies | Variant | Level | Passes when |
|---|---|---|---|---|---|---|
| E-04 | `tests/vrp/test_instance_generator.py` | `T-05` | §11.1 | | L2 | **Done.** Generator in `vrp/generate.py`; five shapes (slack, tight capacity, tight windows, multi-depot, driving hours), each instance a pure function of its seed so a failure found at case 84,213 can be regenerated alone. **10⁵ cases run, zero violations, 55 s** — `make property-soak`. The suite runs 200 by default; `VRP_PROPERTY_CASES` scales it. A separate test asserts the tight shapes leave more orders unplaced than the slack one, so a branch that stopped biting cannot report a green gate having exercised nothing. Placement: **Python**, per criterion 2 |
| E-05 | `tests/vrp/test_benchmark_readers.py` | `T-06` | §11.3 | | L1 | **Done.** Reader in `vrp/benchmarks.py` over `vrplib`; five real instances vendored in `benchmarks/instances/` covering CVRP, VRPTW, PDPTW and VRPSPD. **Best-known values are read from the files, never transcribed** — from the instance `COMMENT` or a sibling `.sol`, and reported as `None` when neither says, since a hand-typed registry is a registry of typos. Measured at 20k iterations, verifier-clean: **E-n22-k4 375 vs optimum 375 (+0.00%)** and **RC208 776 vs 776 (+0.00%)**, both matching the published vehicle count. Planar benchmark coordinates live on `Benchmark`, not `Location`, whose geographic validation stays honest. Placement: **Python** |

---

## 4. Slice 1 — Static core

| # | Example | Task | Satisfies | Variant | Level | Passes when |
|---|---|---|---|---|---|---|
| E-10 | `tests/vrp/test_osrm_adapter.py` | `T-10` | MTX-1…5 | | L1 | **Done.** Adapter in `vrp/osrm.py`, tested against the synthetic engine. `tests/synthetic/grid.osm` gained a disconnected one-way island, which is what gives MTX-5 and MTX-2 a subject at all — before it the map was fully connected and bidirectional. `UNREACHABLE` is negative and `duration()` *raises* on it, so the sentinel cannot reach arithmetic; three examples had been writing `10 ** 9`, the large-finite value MTX-5 names as getting "optimised into" solutions (latent — the CR dataset has no null pairs). Snap distance recorded per location with a threshold warning (MTX-4); version content-addressed over locations+profile+extract (MTX-1, MTX-6). Placement: **Python** — transport stays in the Rust gateway, this is domain translation |
| E-11 | `tests/vrp/test_matrix_cache.py` | `T-11` | MTX-6,7,10 | | L1 | **Done.** Tiling and pair cache in `vrp/matrix.py`. Tiles are square blocks covering every cell exactly once and reassemble identically to the unchunked call; 5,000 locations tiles rather than 422s. Pair cache is LRU, ordered and profile-aware, with the hit rate reported. **MTX-10's 90% implies ≤5% stop churn, not 10%** — reuse is `((n-k)/n)²`, so swapping 2 stops of 20 leaves 81%; the test pins the law rather than a number. Persistent store is `T-11`'s remaining half and needs a decision on where it lives. Placement: **Python** — the gateway's cache keys on the request path, which cannot reuse pairs at any hit rate |
| E-12 | `tests/vrp/test_pyvrp_adapter.py` | `T-12` | FR-01…08 | CVRP, VRPTW | L1 | **Done.** PyVRP adapter in `vrp/solve/`; the verifier accepts the plan the solver produced. Instance is Solomon-shaped but built in the domain model — reading real Solomon files is `E-05`. Placement: **Python**, per criterion 2 |
| E-13 | `tests/vrp/test_objective_tiering.py` | `T-13` | §5.1, FR-13 | PDPTW | L1 | **Done.** Lexicographic objective in `vrp/objective.py`; each level strictly dominates everything beneath it, with bounds derived from the instance and checked to 10^15. §5.2's modes are expressed as *groupings* of tiers rather than a reordering: `MIN_VEHICLES` keeps fleet above operating cost, `MIN_COST` merges them so a vehicle can pay for itself, and `PRIZE_COLLECTING` merges tier 2 in as well so an order can be worth less than its detour. Staged optimisation for >10k stops is `T-13`'s second half and belongs with the solver driver. Showcase: `examples/src/fleet/objective_modes.py`, which puts a 190 km outlier in a day's stops and shows `PRIZE_COLLECTING` abandoning it while every other mode drives out. Note `MIN_COST` and `MIN_VEHICLES` coincide on real data until overtime lands (`T-25`): more vehicles is monotonically worse on distance, so a van cannot repay its fixed cost. Placement: **Python**, per criterion 2 |
| E-14 | `examples/src/fleet/explain/preflight_diagnosis.py` | `T-14` | FR-01, §6.5 | | L1 | **Done.** Every seeded infeasible order returns its specific reason code, not a generic failure |
| E-15 | `tests/vrp/test_solve_api.py` | `T-15` | NFR-03, §9.4 | | L1 | **Done.** Idempotency key honoured; anytime incumbent readable mid-solve |
| E-16 | `tests/vrp/test_benchmark_gate.py` | `T-16` **[GATE]** | CON-9 | | L4 | **Done.** Frozen corpus in `vrp/bench/`, baselines in `benchmarks/BASELINE.md`, gate fails past §11.3's thresholds. Measures regression against our own numbers; gap-to-published-BKS awaits `T-06`. Placement: **Python** |
| E-17 | `tests/vrp/test_determinism.py` | `T-17` | CON-4 | | L3 | **Done.** Byte-identical solution across 100 repeats and across machines |

`E-12` is the reference example — the first end-to-end solve. Everything in
Slice 2 extends its instance rather than starting over.

---

## 5. Slice 2 — Rich constraints

One example per constraint family, each adding to the `E-12` instance so the
interactions are exercised rather than each constraint in isolation.

| # | Example | Task | Satisfies | Variant | Level | Passes when |
|---|---|---|---|---|---|---|
| E-20 | `examples/src/fleet/rich/multi_capacity.py` | `T-20` | FR-02, FR-03 | PDPTW | L2 | **Done.** Multi-dimensional capacity through the adapter with the dimension order pinned (sorted, so two Problems describing one fleet compile identically); pickups compiled as pickups, which they were not before. Peak-load semantics hold: a route netting to zero on totals is correctly rejected when its peak exceeds the hold. **Three perturbation rounds were needed** — the first two passed against a mis-compiled pickup, because the mapper rebuilds the load profile from our own model and so reports the right answer whatever the solver was told. The test that catches it asserts the solver *accepts* a legal peak. Showcase runs 150 stops through E-11's tiling: adding volume takes the plan from 2 vans to 3 without adding a gram, and with collections the cube peaks at 86 of 100 while the totals report 10. Placement: **Python** |
| E-21 | `examples/src/fleet/rich/heterogeneous_fleet.py` | `T-21` | FR-07, FR-08 | MDHVRPTW | L2 | **Done.** Per-vehicle `fixed_cost`, `cost_per_metre`, `cost_per_second`, `overtime_cost_per_second` and `profile` on `Vehicle`; PyVRP takes all four natively. Open routes via a zero-cost sink — PyVRP accepts `end_depot=None` and **silently closes the route** (measured: 4000 m either way on a 2000 m one-way problem). Mixed profiles refused rather than flattened, since a profile is a matrix and `Problem` pins one. Multi-depot and distinct start/end already worked and are pinned as regressions. Cost tests assert a **flip**, not one outcome: the single-direction version passed with the cost wiring removed. Placement: **Python** |
| E-22 | `examples/src/fleet/rich/skills_and_access.py` | `T-22` | FR-10, FR-11 | | L1+L2 | **Done.** Ineligible vehicle never assigned; incompatible orders never share a route; access restriction respected |
| E-23 | `examples/src/fleet/tw/multiple_windows.py` | `T-23` | FR-04, FR-06 | VRPTW | L1+L2 | **Done.** Fixes a defect: soft windows were compiled as hard, so a stop 600 s away with a soft 100 s window came back INFEASIBLE. Disjoint windows are PyVRP mutually-exclusive client groups (members optional, group required). Earliness and lateness costed asymmetrically in the evaluator, and `Tier.SOFT` now carries them instead of a hardcoded 0. Release times already worked and are pinned. **Limitation stated in the adapter:** PyVRP has no soft time windows, so a soft window becomes a wide hard one and the breach is priced afterwards — legal and honestly costed, not optimal in the penalty. Placement: **Python** |
| E-24 | `tests/vrp/test_service_time_model.py` | `T-24` | FR-05, §6.2 | | L1 | **Done.** Fixed + per-unit + vehicle-factor components compose; checked against telematics fixtures |
| E-25 | `examples/src/fleet/rich/hours_of_service.py` | `T-25` **[GATE]** | FR-15, FR-16 | | L1+L2 | **Done.** Rules engine in `vrp/hos/`, both rule sets shipped and selectable by name. Breaks are placed during the walk, so every later arrival shifts by the break duration; a duty that cannot fit one is reported, never silently shortened. INV-7 is now enforced rather than not-applicable, recomputed by the verifier from the timeline's own stamps — it imports the rule sets but not the scheduler, and the import test pins that. Deferred with reasons: EU split breaks (15+30) need a DP, weekly and fortnightly limits need a multi-day horizon (`T-26`), and `AT_FACILITY` placement needs facility candidates in the matrix (§7.2). Placement: **Python**, per criterion 2 |
| E-26 | `tests/vrp/test_initial_state.py` | `T-26` | §6.4 | | L1 | **Done.** Partial-duty carry-over from tachograph input plans correctly |
| E-27 | `examples/src/fleet/rich/prizes_and_priority.py` | `T-27` | FR-12, FR-13 | | L1 | **Done.** Prize-collecting drops the expected low-value orders; tiers never inverted |
| E-28 | `examples/src/fleet/rich/multi_trip.py` | `T-28` | FR-09, **FR-19** | | L2 | **Done.** Reload mid-shift; dock queueing respected; interaction with driver hours verified |
| E-29 | `examples/src/fleet/rich/locks_and_overrides.py` | `T-29` | FR-21, CON-7 | | L1 | **Done.** Every lock kind honoured; a conflicting set returns a minimal irreducible conflict, not a blanket failure |
| E-30 | `tests/vrp/test_engine_agreement.py` | `T-30` | CON-3, §7.3 | | L2 | **Done.** The same domain problem solved by PyVRP and OR-Tools; the verifier accepts both and the canonical evaluator scores them on one scale |

---

## 6. Slice 3 — Scale and quality

| # | Example | Task | Satisfies | Variant | Level | Passes when |
|---|---|---|---|---|---|---|
| E-33 | `tests/vrp/test_neighbourhood_throughput.py` | `T-33` | ALG-2 | | L1 | **Done.** ≥ 10× local-search throughput against the naive baseline, measured not asserted |
| E-34 | `tests/vrp/test_lns_core.py` | `T-34` | ALG-3b | | L4 | **Done.** SISR ruin + greedy-with-blinks recreate matches published results on the frozen corpus |
| E-35 | `examples/src/fleet/alloc/fleet_minimisation.py` | `T-35` | FR-32, §5.2 | | L4 | **Done.** `MIN_VEHICLES` reaches the BKS vehicle count on the benchmark set |
| E-36 | `examples/src/fleet/rich/engine_portfolio.py` | `T-36` | §7.3 | | L1 | **Done.** Incumbents scored by the canonical evaluator, never the engine's own accounting; win rates recorded by instance signature; evidence in `tests/vrp/test_portfolio_runner.py` |
| E-37 | `examples/src/fleet/rich/large_instance_decomposition.py` | `T-37` | §7.6 | | L5 | **Done.** Large instances decompose and recombine with no invariant violation at the boundaries; evidence in `tests/vrp/test_decomposition.py` |
| E-38 | `examples/src/fleet/rich/set_partitioning_polish.py` | `T-38` | ALG-6 | | L5 | **Done.** Never worse than the best pooled trajectory on the frozen corpus (mean reported); ≥ 0.5% on a capacity-pressured 200-customer instance; evidence in `tests/vrp/test_set_partitioning.py` |
| E-39 | `examples/src/fleet/rich/departure_scheduling.py` | `T-39` | ALG-5 | TSP | L1 | **Done.** Duty duration measurably reduced by departure-time choice |
| E-40 | `examples/src/fleet/rich/time_dependent.py` | `T-40` | FR-14 | | L2 | FIFO (no-passing) property holds across every bucket boundary, and the formulation §6.3 forbids is shown overtaking on the same instance. **Done.** The blocker was misread: OSRM cannot serve time-dependent travel, but §12.2 fits multipliers *against* free flow, so the construction never needed a departure-time parameter |
| E-41 | `examples/src/fleet/rich/ev_recharging.py` | `T-41` | FR-20, INV-16 | | L2 | The taper that makes the last fifth of a battery cost three times the first; a 240 km round on 200 km of range ending 20% past empty, with the shortfall at a step rather than in a total; the charge placed late and sized to the rest of the round; every later stop moving by the time on the plug; and a 30 kWh van refused by name. **Done.** |

---

## 7. Slice 4 — Allocation

| # | Example | Task | Satisfies | Variant | Level | Passes when |
|---|---|---|---|---|---|---|
| E-44 | `examples/src/fleet/alloc/fleet_mix.py` | `T-44` | FR-30, FR-32, FR-33 | | L2 | **Done.** Deployment is a decision, not an outcome; own-vs-hire step costs applied; marginal value reported per vehicle |
| E-45 | `examples/src/fleet/alloc/depot_inventory.py` | `T-45` | FR-31 | | L1 | **Done.** A stockout yields `DEPOT_STOCKOUT`, never a silent over-allocation; evidence in `tests/vrp/test_depot_inventory.py` |
| E-46 | `examples/src/fleet/alloc/tactical_sizing.py` | `T-46` | FR-34 | | L5 | **Done.** Cost/service Pareto front over ≥ 30 days × ≥ 10 fleet mixes |
| E-47 | `examples/src/fleet/alloc/territories.py` | `T-47` | FR-17, FR-18, FR-35 | | L2 | **Done.** Territories balanced on duration, distance and stop count; driver-customer stability measured across periods **Section 5 draws it** (`territories_map.html`): the same stops under both plans as two toggleable layers, each van's stops coloured and its convex hull shaded. The caption is measured rather than asserted -- an average van's hull covers **13% of the round by territory against 39% round-robin** -- because a claim about a shape drifts from the data silently. Map helpers are `examples/src/maps.py`, shared with `config` and `dataset`; its geometry is tested in `tests/test_example_maps.py`. |

---

## 8. Slice 5 — Dynamic operation

| # | Example | Task | Satisfies | Variant | Level | Passes when |
|---|---|---|---|---|---|---|
| E-50 | `examples/src/fleet/dynamic/committed_state.py` | `T-50` | DYN-4 | | L2 | **Done.** No executed stop ever moves, proven over the replay corpus; evidence in `tests/vrp/test_committed_state.py` |
| E-51 | `examples/src/fleet/dynamic/dispatch_waves.py` | `T-51` | DYN-1, DYN-2, **FR-22** | | L2 | **Done.** Zero must-go postponements across the replay corpus; dispatched-now vs postponed decided explicitly |
| E-52 | `tests/vrp/test_baseline_policies.py` | `T-52` | §8.2 | | L1 | **Done.** Greedy, lazy and random wired into the replayer as permanent baselines |
| E-53 | `examples/src/fleet/dynamic/replay_policies.py` | `T-53` **[GATE]** | DYN-6 | | L6 | **Done.** Deterministic replay of 90 historical days; policies comparable on one scale; evidence in `tests/vrp/test_historical_replayer.py` |
| E-54 | `examples/src/fleet/dynamic/sample_scenario_policy.py` | `T-54` | §8.2 | | L6 | **Done.** Beats greedy and lazy on the replay corpus, with the result written down |
| E-55 | `examples/src/fleet/dynamic/prize_collecting_epoch.py` | `T-55` | §8.2 | | L6 | **Done.** Comparable or better than the sample-scenario policy |
| E-56 | `examples/src/fleet/dynamic/breakdown_at_eleven.py` | `T-56` | DYN-5, §8.3 | | L1 | **Done.** p95 ≤ 30 s with 90% of the plan locked; evidence in `tests/vrp/test_reoptimisation_latency.py` |
| E-57 | `examples/src/fleet/dynamic/churn_tradeoff.py` | `T-57` | §8.3 | | L5 | **Done.** Churn/cost curve produced so operations can choose a point |

---

## 9. Slice 6 — Learning, explanation, operations

| # | Example | Task | Satisfies | Variant | Level | Passes when |
|---|---|---|---|---|---|---|
| E-60 | `examples/src/fleet/explain/why_unassigned.py` | `T-60` | CON-5, FR-36 | | L1 | **Done.** Per-order rationale, marginal cost, and `would_fit_if` for every rejected order |
| E-61 | `examples/src/fleet/learn/plan_adherence.py` | `T-61` | CON-6, §12.4 | | L6 | **Done.** Adherence computed per depot, driver and territory from telematics; evidence in `tests/vrp/test_plan_adherence.py` |
| E-62 | `examples/src/fleet/learn/service_time_calibration.py` | `T-62` | §12.1 | | L1 | **Done.** Monthly re-fit reproduces a known fixture; drift alerts fire |
| E-63 | `examples/src/fleet/learn/speed_calibration.py` | `T-63` | §12.2 | | L1 | Where a speed profile's numbers come from: multipliers fitted from what the vans did, against what the engine believed. Shows the arcs that cross the hour being discarded, the fit recovering the traffic that produced it, a held-out week scoring 0s against 880s for a week of gridlock, and the fit refusing to average arc classes together. **Done.** |
| E-64 | `examples/src/fleet/learn/zone_sequence_prior.py` | `T-64` | §12.4 | | L6 | **Done.** Adherence improves with no verifier regression; advisory only, never a hard constraint; evidence in `tests/vrp/test_zone_prior.py` |
| E-65 | `examples/src/fleet/learn/canary_rollout.py` | `T-65` | §11.4 | | L6+L7 | **Done.** One depot canary completed with a written go/no-go; evidence in `tests/vrp/test_shadow_and_canary.py` |
| E-66 | `examples/src/fleet/verify/external_plan.py` | `T-66` | §9.4, CON-1 | | L1 | **Done.** An externally supplied plan is verifiable through the public endpoint; evidence in `tests/vrp/test_verify_endpoint.py` |
| E-67 | `examples/src/fleet/infra/accelerator_profile.py` | `T-67` | NFR-09, §7.3 | | L4 | A negative requirement demonstrated on the machine that lacks the thing: with the profile off the import system is never asked for `cuopt` at all (shown with a watcher carrying its own sentinel), the plan is the same stops in the same order, and switching it on without the library reports the fallback instead of hiding it. The engine refuses by name — no cuOpt has ever run against this code. **Done.** accelerator profile, and no GPU is present in this environment |

---

## 10. Coverage check

Every functional requirement in SDD §3 maps to at least one example:

| Requirement | Example |
|---|---|
| FR-01…FR-08 | E-12, plus E-20 (02, 03), E-21 (07, 08) |
| FR-09 | E-28 |
| FR-10, FR-11 | E-22 |
| FR-12, FR-13 | E-27, E-13 |
| FR-14 | E-40 *(blocked upstream)* |
| FR-15, FR-16 | E-25 |
| FR-17, FR-18 | E-47 |
| FR-19 | E-28 |
| FR-20 | **none — see below** |
| FR-21 | E-29 |
| FR-22 | E-51 |
| FR-30, FR-32, FR-33 | E-44, E-35 |
| FR-31 | E-45 |
| FR-34 | E-46 |
| FR-35 | E-47 |
| FR-36 | E-60, E-44 |

### Three requirements the SDD's own backlog does not trace

Checking §3 against §13 turned up a traceability gap in the specification
itself:

**Closed in SDD 1.2.** `FR-19` and `FR-22` are now claimed by `T-28` and `T-51`,
whose titles already described the work. `FR-20` was given `T-41`, marked
`COULD` and flagged as the only task with no data source in the current stack.
The examples above (E-28, E-51) trace to them, and `E-41` covers `T-41`.

---

## 11. Sequencing

The dependency structure is the SDD's, not mine. Three properties are worth
stating because they are what make this a TDD plan rather than a list:

1. **E-03 before everything.** The verifier is the oracle. Without it, every
   later example asserts what the solver believes rather than what is true.
2. **The gates are real.** `T-04`, `T-16`, `T-25` and `T-53` block their
   successors. E-16 in particular — a benchmark baseline — must exist before any
   quality claim in Slice 3 means anything.
3. **Slices 2 through 6 are largely parallel once Slice 1 lands.** The rich
   constraints (E-20…E-30) touch different parts of the model, so they can be
   written concurrently by different people, provided each is judged by the same
   verifier.

### If only a few are ever written

In order: **E-03** (verifier), **E-12** (the first real solve), **E-16**
(benchmark gate), **E-25** (hours of service — the constraint most likely to be
got subtly wrong, and the one with legal consequences).

---

## 11a. Slice 7 — the examples the catalogue's requirements will need

`T-72`–`T-78` were written into the SDD backlog from what the scenario corpus
found (`CAT-VRP-003` §12.2 and the four strict xfails in `tests/vrp/`). None is
started, so none has an example yet — these rows say what each will have to
show, which is the same discipline as the rest of this document: the example is
written first and fails.

| Ex | Where | Task | Requirement | Level | Passes when |
|---|---|---|---|---|---|
| `E-72` | `examples/src/fleet/p0/must_work_at_v1.py` (`UC-019`, `UC-134`) | `T-72` | FR-10, FR-11, FR-31 | L1 | A plan the verifier would reject on skills, site access or depot stock is never built in the first place; class incompatibility is refused instead, and `examples/src/fleet/adversarial/pathological_instances.py` shows that half. **Done.** No `eligibility.py`: the demonstration is two operations, and it reads better beside the others than alone |
| `E-73` | `examples/src/fleet/rich/multi_period.py` | `T-73` | FR-23 | L2 | A clustered schedule and a spread one both make four visits; only one keeps the interval, and an unkeepable contract is refused rather than reported. **Done.** |
| `E-74` | `examples/src/fleet/rich/ride_time.py` | `T-74` | FR-24 | L2 | A shipment's time aboard is bounded independently of its delivery window; the search is told a sound deadline and the verifier measures the journey exactly. **Done.** |
| `E-75` | `examples/src/fleet/rich/priority_sources.py` | `T-75` | FR-25 | L1 | Three orders equal on tier are ordered by source; a statutory duty cannot be priced; an SLA window is computed from the fault timestamp. **Done.** |
| `E-76` | `examples/src/fleet/rich/synchronisation.py` | `T-76` | FR-26 | L2 | A plain solve leaves the bike departing before the lorry arrives; the loop holds it until the handover is done, and the half no window can express is refused by name. **Done.** |
| `E-77` | `examples/src/fleet/dynamic/preemption.py` | `T-77` | FR-27 | L2 | An emergency displaces planned work, what it displaced is named, and with room the displaced work is re-planned rather than dropped. **Done.** |
| `E-78` | `examples/src/fleet/p0/must_work_at_v1.py` (`UC-171`) | `T-78` | FR-21, FR-30, FR-32 | L2 | A recovery never asks a loaded vehicle to be repacked, and serves what the remaining fleet can carry. **Done.** No `absent_driver.py`: `UC-171` is a P0 operation and belongs with the other thirteen. The claim was never false — the measurement was, because a free re-plan was being scored on a freedom the depot does not have |
| `E-79` | `examples/src/fleet/infra/degraded_matrix.py` | `T-79` | NFR-04, MTX-11 | L1 | A provider that dies mid-build keeps the tiles that arrived, leaves the rest `UNREACHABLE` rather than guessing, and carries the reason out to the plan a dispatcher reads. **Done.** |
| `E-80` | `examples/src/fleet/rich/time_dependent.py` (§2–3) | `T-80` | FR-14, §7.5 | L2 | The same stops finish later through a peak than at free flow, the verifier agrees with the evaluator about every arrival, and §7.5's bound is measured on route-shaped arcs. **Done.** No separate file: it is the same construction `E-40` shows, one layer up |
| `E-82` | `examples/src/fleet/rich/planning_under_congestion.py` | `T-82` | FR-14, §7.5, NFR-01 | L2 | Five stops and one 11:00 deadline: free flow says nearest-first, the peak says buy the far stop while the day is young. The free-flow plan is the *faster* of the two and is fifteen minutes late. Also shows the bound holding at every departure, and what IGP costs the polish. **Done.** |
| `E-83` | `examples/src/fleet/rich/arc_class_profiles.py` | `T-83` | §6.3, §12.2, MTX-9 | L2 | The measurement that unblocked the task — sixteen of twenty-seven fixtures span more than one arc class; one profile forced to slow a side street exactly as much as a motorway; three profiles saying what a driver would; and a mapping that misses a class refused rather than defaulted to free flow. **Done.** |
| `E-91` | `examples/src/fleet/infra/process_portfolio.py` | `T-91` | NFR-05, §7.7 | L1 | The same pure-Python work at 0.99x across four threads and 3.32x across four processes on a real round; the worker's pid proving it is another interpreter; and the two constraints processes impose — an unimportable engine refused by name, and the `__main__` guard the file itself demonstrates. **Done.** |
| `E-85` | `examples/src/fleet/dynamic/insertion_quote.py` | `T-85` | NFR-02, §9.4 | L1 | One route gaining one stop and every other route untouched; the raw objective saying −998,800 against a real price of 1,200; a refusal when the van is full; and p95 104 ms against NFR-02's 2 s on 400 real deliveries across 40 vans. **Done.** |
| `E-92` | `examples/src/fleet/infra/decomposition_queue.py` | `T-92` | NFR-05, §7.7 | L1 | The same plan at three widths; a barrier a pool of one cannot pass; the disjoint vehicles that make the merge order-independent; and the speed-up measured across three instance sizes — 0.66x on `uc074`, 2.25x at 300 real deliveries, 1.92x at 600. **Done.** |
| `E-86` | `examples/src/fleet/infra/portfolio_parallelism.py` | `T-86` | NFR-05, §7.7 | L1 | The barrier that a pool of two passes and a pool of one times out on; the peak-in-flight count against the bound; the same winner and the same report order at three widths; and the speed-up measured for both kinds of engine on real deliveries — 3.13x for PyVRP, 1.00x for the pure-Python LNS. **Done.** |
| `E-87` | `examples/src/fleet/infra/run_record.py` | `T-87` | NFR-06, CON-4 | L1 | A real run's trajectory over a real round (21,746 → 5,786 across ten incumbents); the same seed twice, whole records differing and replayable halves identical; the hit rate and violation counts shown moving rather than merely present; and the same plan with and without a recorder. **Done.** |
| `E-89` | `examples/src/fleet/infra/plan_snapshots.py` | `T-89` | NFR-08, CON-4 | L1 | The thirty fields the model's round trip used to lose; a 4.7 KB sealed record that rebuilds equal to the original; one digit changed in a retained file and the reader refusing rather than replaying it; and a plan re-derived from the record alone, twice, identically. **Done.** |
| `E-90` | `tests/test_api_versioning.py` | `T-90` | NFR-10, §9.4 | L1 | **The black-box test is the example**, and deliberately: what T-90 delivers is an HTTP contract, so the demonstration is a live exchange with the compiled binary — both spellings returning the same body, the deprecated one carrying `Deprecation`/`Sunset`/`Link` and the versioned one carrying none, and the probes advised to migrate nowhere. A standalone script would re-implement the harness's process management to show the same headers. Recorded as an exception rather than skipped, like `pathological_instances.py`. **Done.** |
| `E-93` | `examples/src/fleet/tw/sla_windows.py` (`T-93`: `tests/vrp/test_window_attainment.py`) | `T-93` | §6.2, AC-4.2, `UC-116`, FR-25 | L1+L2 | **Done.** `evaluator.window_attainment` counts promises kept along a timeline, unpriced and blind to hardness. The gap it closes: lateness existed only as a *price*, and `soft_penalties` costs SOFT windows alone -- a HARD window may not carry a rate (`TimeWindow.__post_init__` forbids it) and a SOFT one may carry zero, so a stop served two hours after its window shut was accounted at **zero** by every lateness report in the codebase, `triggers._lateness` included. Measured against the last window that closed, not the first, so a disjoint pair is not overstated by the gap between them; a stop with no window is not counted as promised, since scoring the unconstrained as punctual would let a windowless plan report perfect service. `scenarios.service_level` stays an **assignment** rate and its docstring now says so instead of claiming "within window". Four perturbations checked: first-closed-window, unwindowed-counts-as-kept, SOFT-only, and on-time-by-lateness -- each fails a distinct test. Placement: **Python** `vrp/evaluator.py`, per criterion 2 -- it is a measure over a timeline, exactly like `route_metrics`, and CON-1 keeps it out of the verifier. The example puts the corpus `priority` column to work for the first time -- `express`/`standard`/`scheduled` as three different promises, windows derived per order at intake from `order_id` sequence, response targets read off a calibration round's own service times rather than typed in. Measured on 24 spread stops around Guadalupe: keeping the promises costs **46% more distance** than the round that promised nothing; priced instead of enforced, 15 of 24 break and **express comes last at 0 ppt**; and the same target written as one fixed window -- `UC-116`'s named failure -- runs **1,031 minutes late across 14 stops and prices it at exactly zero**, which is the example's argument for the measure existing |
| `E-94` | `examples/src/fleet/tw/envelope_round.py` | `T-23` | FR-02, FR-04, FR-05 | L2 | **Done.** Signed-document delivery, and the first example here whose round is **service-bound rather than travel-bound**: measured at 120 stops, signing is **86% of the day and riding 2%**, so the distance objective every other fleet example turns on is rounding error. Three things it demonstrates that no other example does. **Kilograms run out of resolution** -- 200 g rounds up to 1 kg under `dataset.load_kg`, identical for every letter, so the dimension carries nothing; the answer is grams, the same finer-unit move `rich/multi_capacity.py` makes for volume. **Capacity cannot bind** -- 8.2 kg carried against a 25 kg satchel -- and is reported precisely so a reader who assumes a capacity dimension binds can see that it does not. **The lunch hour is emergent**: offices close 12:00-13:00 as disjoint windows (FR-04), and all three couriers then stand idle **50 of those 60 minutes** with no break modelled at all -- which matters because `vrp.hos` triggers on accumulated driving (4.5 h under EU-561) and this courier rides under an hour a day, so a placed break is not expressible. Sizing counts only plans the verifier accepts: one and two couriers come back INFEASIBLE with arrivals clamped to noon while still reporting 120 stops placed, which is `E-23`'s warning that a solver's stop count is what it attempted rather than what is achievable. **Section 5 draws it** (`envelope_round_map.html`): couriers and hulls on one layer, before/after the closure on another. The measurement is the surprise -- an average courier's hull covers **28% of the round against 29% for a deliberate geographic carve-up of the same stops and 89% for a round-robin scattering**, all three computed here rather than quoted from `E-47`, whose geometry would not transfer. Riding is 2% of this round's cost, so the solver had almost no reason to prefer a compact day and produced one anyway: compactness is a by-product of packing a clock, worth knowing before anyone buys it again with a territory constraint. |

---

## 12. Non-goals

- This plan does not schedule the work or estimate it.
- It does not choose between PyVRP and OR-Tools; E-30 exists precisely so the
  choice can be made on evidence.
- It assumes the SDD's architecture. If the platform is not built, the fit/gap
  document's tier-A and tier-B examples remain the cheaper way to demonstrate
  what today's gateway can and cannot do.
