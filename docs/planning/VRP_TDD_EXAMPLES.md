# VRP SDD — example-driven implementation plan

One runnable example per unit of functionality in
[`../vrp-spec-driven-development.md`](../vrp-spec-driven-development.md), ordered
so each can be written **before** the code that satisfies it.

Companion to [VRP_SDD_FIT_GAP.md](VRP_SDD_FIT_GAP.md), which measures how far
today's implementation is from the specification. This document is the route
from one to the other.

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
| **E-03b** | `tests/vrp/test_evaluator_verifier_agreement.py` | §11.2 | L2 | 300 generated instances: every timeline the evaluator builds satisfies the verifier, and the two recompute the same distance |

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

| # | Example | Task | Satisfies | Level | Passes when |
|---|---|---|---|---|---|
| E-04 | `tests/vrp/test_instance_generator.py` | `T-05` | §11.1 | L2 | 10⁵ generated instances produce zero invariant violations |
| E-05 | `tests/vrp/test_benchmark_readers.py` | `T-06` | §11.3 | L1 | Solomon, CVRPLIB, VRPLIB, Li&Lim and GH sets all parse; BKS registry loads and matches published values |

---

## 4. Slice 1 — Static core

| # | Example | Task | Satisfies | Level | Passes when |
|---|---|---|---|---|---|
| E-10 | `tests/vrp/test_osrm_adapter.py` | `T-10` | MTX-1…5 | L1 | Matrix built against a live engine; unreachable pairs carry the sentinel, not zero; snapping recorded |
| E-11 | `tests/vrp/test_matrix_cache.py` | `T-11` | MTX-6,7,10 | L1 | Content-addressed key; chunking reassembles identically to an unchunked call |
| E-12 | `tests/vrp/test_pyvrp_adapter.py` | `T-12` | FR-01…08 | L1 | **Done.** PyVRP adapter in `vrp/solve/`; the verifier accepts the plan the solver produced. Instance is Solomon-shaped but built in the domain model — reading real Solomon files is `E-05`. Placement: **Python**, per criterion 2 |
| E-13 | `tests/vrp/test_objective_tiering.py` | `T-13` | §5.1, FR-13 | L1 | **Done.** Lexicographic objective in `vrp/objective.py`; each level strictly dominates everything beneath it, with bounds derived from the instance and checked to 10^15. §5.2's modes are expressed as *groupings* of tiers rather than a reordering: `MIN_VEHICLES` keeps fleet above operating cost, `MIN_COST` merges them so a vehicle can pay for itself, and `PRIZE_COLLECTING` merges tier 2 in as well so an order can be worth less than its detour. Staged optimisation for >10k stops is `T-13`'s second half and belongs with the solver driver. Placement: **Python**, per criterion 2 |
| E-14 | `examples/src/fleet/explain/preflight_diagnosis.py` | `T-14` | FR-01, §6.5 | L1 | Every seeded infeasible order returns its specific reason code, not a generic failure |
| E-15 | `tests/vrp/test_solve_api.py` | `T-15` | NFR-03, §9.4 | L1 | Idempotency key honoured; anytime incumbent readable mid-solve |
| E-16 | `tests/vrp/test_benchmark_gate.py` | `T-16` **[GATE]** | CON-9 | L4 | **Done.** Frozen corpus in `vrp/bench/`, baselines in `benchmarks/BASELINE.md`, gate fails past §11.3's thresholds. Measures regression against our own numbers; gap-to-published-BKS awaits `T-06`. Placement: **Python** |
| E-17 | `tests/vrp/test_determinism.py` | `T-17` | CON-4 | L3 | Byte-identical solution across 100 repeats and across machines |

`E-12` is the reference example — the first end-to-end solve. Everything in
Slice 2 extends its instance rather than starting over.

---

## 5. Slice 2 — Rich constraints

One example per constraint family, each adding to the `E-12` instance so the
interactions are exercised rather than each constraint in isolation.

| # | Example | Task | Satisfies | Level | Passes when |
|---|---|---|---|---|---|
| E-20 | `examples/src/fleet/rich/multi_capacity.py` | `T-20` | FR-02, FR-03 | L2 | Peak-load property holds on simultaneous pickup and delivery; load is non-monotonic along the route |
| E-21 | `examples/src/fleet/rich/heterogeneous_fleet.py` | `T-21` | FR-07, FR-08 | L2 | Per-vehicle capacity and cost honoured; open routes and distinct start/end verified |
| E-22 | `examples/src/fleet/rich/skills_and_access.py` | `T-22` | FR-10, FR-11 | L1+L2 | Ineligible vehicle never assigned; incompatible orders never share a route; access restriction respected |
| E-23 | `examples/src/fleet/tw/multiple_windows.py` | `T-23` | FR-04, FR-06 | L1+L2 | Disjoint windows honoured; soft-window penalties asymmetric as specified; release times respected |
| E-24 | `tests/vrp/test_service_time_model.py` | `T-24` | FR-05, §6.2 | L1 | Fixed + per-unit + vehicle-factor components compose; checked against telematics fixtures |
| E-25 | `examples/src/fleet/rich/hours_of_service.py` | `T-25` **[GATE]** | FR-15, FR-16 | L1+L2 | `EU-561` and `US-HOS` fixtures both legal under the verifier; breaks inserted **inside** evaluation, not after |
| E-26 | `tests/vrp/test_initial_state.py` | `T-26` | §6.4 | L1 | Partial-duty carry-over from tachograph input plans correctly |
| E-27 | `examples/src/fleet/rich/prizes_and_priority.py` | `T-27` | FR-12, FR-13 | L1 | Prize-collecting drops the expected low-value orders; tiers never inverted |
| E-28 | `examples/src/fleet/rich/multi_trip.py` | `T-28` | FR-09, **FR-19** | L2 | Reload mid-shift; dock queueing respected; interaction with driver hours verified |
| E-29 | `examples/src/fleet/rich/locks_and_overrides.py` | `T-29` | FR-21, CON-7 | L1 | Every lock kind honoured; a conflicting set returns a minimal irreducible conflict, not a blanket failure |
| E-30 | `tests/vrp/test_engine_agreement.py` | `T-30` | CON-3, §7.3 | L2 | The same domain problem solved by PyVRP and OR-Tools; the verifier accepts both and the canonical evaluator scores them on one scale |

---

## 6. Slice 3 — Scale and quality

| # | Example | Task | Satisfies | Level | Passes when |
|---|---|---|---|---|---|
| E-33 | `tests/vrp/test_neighbourhood_throughput.py` | `T-33` | ALG-2 | L1 | ≥ 10× local-search throughput against the naive baseline, measured not asserted |
| E-34 | `tests/vrp/test_lns_core.py` | `T-34` | ALG-3b | L4 | SISR ruin + greedy-with-blinks recreate matches published results on the frozen corpus |
| E-35 | `examples/src/fleet/alloc/fleet_minimisation.py` | `T-35` | FR-32, §5.2 | L4 | `MIN_VEHICLES` reaches the BKS vehicle count on the benchmark set |
| E-36 | `tests/vrp/test_portfolio_runner.py` | `T-36` | §7.3 | L1 | Incumbents scored by the canonical evaluator, never the engine's own accounting; win rates recorded by instance signature |
| E-37 | `tests/vrp/test_decomposition.py` | `T-37` | §7.6 | L5 | Large instances decompose and recombine with no invariant violation at the boundaries |
| E-38 | `tests/vrp/test_set_partitioning.py` | `T-38` | ALG-6 | L5 | ≥ 0.5% mean improvement over the route pool on the frozen corpus |
| E-39 | `examples/src/fleet/rich/departure_scheduling.py` | `T-39` | ALG-5 | L1 | Duty duration measurably reduced by departure-time choice |
| E-40 | `examples/src/fleet/rich/time_dependent.py` | `T-40` | FR-14 | L2 | FIFO (no-passing) property holds across every bucket boundary. **Blocked**: OSRM has no departure-time parameter — see the wishlist |
| E-41 | `examples/src/fleet/rich/ev_recharging.py` | `T-41` | FR-20 | L2 | Range never violated on a generated EV corpus; charging time appears in the duty timeline rather than being added afterwards. **`COULD` priority, and blocked** on charger locations and charging curves, which this stack has no source for |

---

## 7. Slice 4 — Allocation

| # | Example | Task | Satisfies | Level | Passes when |
|---|---|---|---|---|---|
| E-44 | `examples/src/fleet/alloc/fleet_mix.py` | `T-44` | FR-30, FR-32, FR-33 | L2 | Deployment is a decision, not an outcome; own-vs-hire step costs applied; marginal value reported per vehicle |
| E-45 | `tests/vrp/test_depot_inventory.py` | `T-45` | FR-31 | L1 | A stockout yields `DEPOT_STOCKOUT`, never a silent over-allocation |
| E-46 | `examples/src/fleet/alloc/tactical_sizing.py` | `T-46` | FR-34 | L5 | Cost/service Pareto front over ≥ 30 days × ≥ 10 fleet mixes |
| E-47 | `examples/src/fleet/alloc/territories.py` | `T-47` | FR-17, FR-18, FR-35 | L2 | Territories balanced on duration, distance and stop count; driver-customer stability measured across periods |

---

## 8. Slice 5 — Dynamic operation

| # | Example | Task | Satisfies | Level | Passes when |
|---|---|---|---|---|---|
| E-50 | `tests/vrp/test_committed_state.py` | `T-50` | DYN-4 | L2 | No executed stop ever moves, proven over the replay corpus |
| E-51 | `examples/src/fleet/dynamic/dispatch_waves.py` | `T-51` | DYN-1, DYN-2, **FR-22** | L2 | Zero must-go postponements across the replay corpus; dispatched-now vs postponed decided explicitly |
| E-52 | `tests/vrp/test_baseline_policies.py` | `T-52` | §8.2 | L1 | Greedy, lazy and random wired into the replayer as permanent baselines |
| E-53 | `tests/vrp/test_historical_replayer.py` | `T-53` **[GATE]** | DYN-6 | L6 | Deterministic replay of 90 historical days; policies comparable on one scale |
| E-54 | `examples/src/fleet/dynamic/sample_scenario_policy.py` | `T-54` | §8.2 | L6 | Beats greedy and lazy on the replay corpus, with the result written down |
| E-55 | `examples/src/fleet/dynamic/prize_collecting_epoch.py` | `T-55` | §8.2 | L6 | Comparable or better than the sample-scenario policy |
| E-56 | `tests/vrp/test_reoptimisation_latency.py` | `T-56` | DYN-5, §8.3 | L1 | p95 ≤ 30 s with 90% of the plan locked |
| E-57 | `examples/src/fleet/dynamic/churn_tradeoff.py` | `T-57` | §8.3 | L5 | Churn/cost curve produced so operations can choose a point |

---

## 9. Slice 6 — Learning, explanation, operations

| # | Example | Task | Satisfies | Level | Passes when |
|---|---|---|---|---|---|
| E-60 | `examples/src/fleet/explain/why_unassigned.py` | `T-60` | CON-5, FR-36 | L1 | Per-order rationale, marginal cost, and `would_fit_if` for every rejected order |
| E-61 | `tests/vrp/test_plan_adherence.py` | `T-61` | CON-6, §12.4 | L6 | Adherence computed per depot, driver and territory from telematics |
| E-62 | `examples/src/fleet/learn/service_time_calibration.py` | `T-62` | §12.1 | L1 | Monthly re-fit reproduces a known fixture; drift alerts fire |
| E-63 | `examples/src/fleet/learn/speed_calibration.py` | `T-63` | §12.2 | L1 | Weekly re-fit preserves FIFO; held-out validation reported |
| E-64 | `tests/vrp/test_zone_prior.py` | `T-64` | §12.4 | L6 | Adherence improves with no verifier regression; advisory only, never a hard constraint |
| E-65 | `tests/vrp/test_shadow_and_canary.py` | `T-65` | §11.4 | L6+L7 | One depot canary completed with a written go/no-go |
| E-66 | `tests/vrp/test_verify_endpoint.py` | `T-66` | §9.4, CON-1 | L1 | An externally supplied plan is verifiable through the public endpoint |
| E-67 | `tests/vrp/test_cuopt_profile.py` | `T-67` | NFR-09 | L4 | Feature-flagged; the CPU path is bit-identical when disabled |

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

## 12. Non-goals

- This plan does not schedule the work or estimate it.
- It does not choose between PyVRP and OR-Tools; E-30 exists precisely so the
  choice can be made on evidence.
- It assumes the SDD's architecture. If the platform is not built, the fit/gap
  document's tier-A and tier-B examples remain the cheaper way to demonstrate
  what today's gateway can and cannot do.
