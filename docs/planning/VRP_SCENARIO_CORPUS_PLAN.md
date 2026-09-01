# Folding the catalogue into the engine — the scenario corpus phase

Companion to [`../TDD/vrp-catalogue-v2.1.md`](../TDD/vrp-catalogue-v2.1.md)
(`CAT-VRP-003`), [`../vrp-spec-driven-development.md`](../vrp-spec-driven-development.md)
(`SDD-VRP-001`) and [`VRP_TDD_EXAMPLES.md`](VRP_TDD_EXAMPLES.md).

Written 2026-09-01 against `6341807`.

---

## 1. The finding this phase exists to fix

The catalogue is inert. It states its purpose as *"Ground requirements in real
operations; **generate the fixture corpus**"*, and §1 says its entries *"do
double duty: they justify requirements in the design document, and they specify
fixtures for the test corpus."* The first half happened. The second did not:

```
$ grep -rn "UC-[0-9]" --include='*.py' vrp/ tests/ | wc -l
0
```

741 VRP tests, 53 test modules, and not one of them knows a `UC-nnn` exists. The
`E-xx` examples are wired the other way round — 48 distinct example ids appear in
the test bodies — so this is not a house style, it is a gap. Every requirement is
demonstrated; no *operation* is.

Two consequences follow, and both are already visible:

- **Coverage is claimed against requirements, not against reality.** `FR-02` has
  45 citing scenarios and a passing `test_multi_capacity.py`. Nothing connects
  the two, so nothing can report that a requirement landed without an operation
  behind it — which is precisely what §13.6 asks CI to report.
- **The strongest test oracle in the repository is unused.** Every entry carries
  a `Breaks` line naming a concrete wrong answer from an operation where that bug
  shipped. §1: *"'Test that capacity works' is a weak test. 'Test that a route
  whose total load fits but whose peak load exceeds capacity is rejected' is a
  real one."* 142 of those, written and unspent.

A third, narrower finding: **the header's scenario count is not true of the
data.** The document says 157 (142 operational + 15 adversarial); `scenarios.jsonl`
has 142 rows. The 15 adversarial instances of §11 (`UC-060`…`UC-074`) are a prose
table outside the entry schema, so no query, filter or gate can see them. They are
also the cheapest and highest-value tests in the whole catalogue.

The catalogue already contains the plan for all of this. §13 "Building the fixture
corpus" is six numbered directives, and this document is their implementation
order, nothing more inventive than that.

---

## 2. What is already true, so the phase is not over-scoped

The P0 scenarios are **expressible with today's domain model**. This was checked
field by field rather than assumed:

| P0 need | Already in `vrp/model.py` |
|---|---|
| Compartments as dimensions (`UC-002`) | `Vehicle.capacities`, multi-dimensional |
| Multi-trip to the tip (`UC-013`) | `reload_locations`, `max_reloads`, `reload_duration` |
| Peak load, not totals (`UC-004`) | `Order.pickup` / `delivery`, signed load |
| Open tour from current position (`UC-087`) | `Vehicle.open_route`, `start_location_id` |
| Home-start technicians (`UC-019`) | per-vehicle `start_location_id` / `end_location_id` |
| Hard bay hours (`UC-003`) | `TimeWindow.hardness`, `Vehicle.shift`, `hos_rules` |
| Depot choice against stock (`UC-134`) | `T-45` depot allocation, `DEPOT_STOCKOUT` |
| Executed work is immovable (`UC-032`, `UC-171`) | `vrp/committed.py`, `vrp/locks.py` |
| Sequence realism (`UC-075`, `UC-009`) | `vrp/adherence.py`, `vrp/zones.py` |

So Phases 1–4 are **fixture work, not capability work**: writing the operation
down and proving the `Breaks` failure is actually caught. New capability is
Phase 5, and it is small and well-evidenced.

---

## 3. Phase 1 — the 15 adversarial instances (§13.2)

*"Pathological instances (§11) are hand-built and tiny, in the fast tier, run on
every commit."*

These come first because they are the smallest instances in the catalogue, they
need no new machinery, and a spot check says most of them are unbuilt:

| Probe of `tests/vrp/` | Result |
|---|---|
| zero-width / inverted windows | no match |
| antimeridian / high latitude | no match |
| many orders at one address, zero-distance arcs | no match |
| zero available vehicles | no match |
| midnight crossing / DST | matches in `test_compatibility.py`, `test_route_polish.py` — unrelated senses of the word |

Others are covered incidentally and only need naming: `CAPACITY_EXCEEDED` and
`DEPOT_STOCKOUT` exist in `vrp/diagnose.py`, minimal conflicting lock sets in
`test_lock_conflicts.py`, the decomposition threshold in `test_decomposition.py`.

### 1a. Make them machine-readable

Author `UC-060`…`UC-074` as real entries in `vrp-catalogue-v2.1.src.md` §11, using
the §0.3 field set. Their existing `Required behaviour` column becomes `Breaks`
(inverted: it already names the wrong answer — *"never a large finite distance"*,
*"never silent haversine"*, *"never a crash"*).

This needs one contained change to `build_catalogue.py`: a `PATHOLOGICAL` value in
the `variant` vocabulary, excluded from the §2 and §12.1 variant indexes and
reported on its own row. Assigning them real variants instead would corrupt the
variant audit that §2.1 exists to defend.

Ripples, all of them true statements rather than drift, and all regenerated by
`make catalogue`: front-matter counts 142 → 157; `by_tier` P0 14 → 29 (these
*must work at v1*, so P0 is the honest tier); the header's 157 becomes true of the
data for the first time.

**The alternative is cheaper and wrong**: leave them in prose and hardcode the 15
ids in the Phase 2 gate. That is a second source of truth for which scenarios must
exist, and it will drift from the catalogue the first time anyone adds one.

### 1b. Write them

One file, `tests/vrp/test_pathological.py`, fifteen tests named for their ids.
Each is a handful of stops. Expect real failures — that is the return on this
phase, and each one is either a defect to fix or an accepted limitation recorded
in the entry's `status_note`.

**Acceptance:** `make catalogue` emits 157 scenarios and exits zero; fifteen named
tests exist; every failure is either fixed or has a written `status_note`.

---

## 4. Phase 2 — the loader and the coverage gate (§13.6) — **shipped**

*"The coverage matrix is regenerated in CI from requirement tags on fixtures, so a
requirement landing without a scenario is reported rather than passing unnoticed."*

`vrp/bench/catalogue.py` — reads `docs/TDD/scenarios.jsonl` into a frozen
`Scenario` dataclass, with the §0.5 query patterns as functions (`by_tier`,
`by_variant`, `citing`, `tagged`). Resolve the path with
`Path(__file__).resolve().parents[2]`, the idiom already used in
`tests/conftest_synthetic.py`; `vrp/bench/runner.py`'s cwd-relative `Path(...)` is
fine for a CLI entry point and wrong for a module imported by tests.

`vrp/bench/fixtures.py` — the registry: `UC id → (size) → Problem`. Empty at the
end of this phase except for Phase 1's fifteen.

`tests/vrp/test_catalogue_coverage.py` — four assertions, and the choice of which
fail and which merely report is the whole design of the gate:

1. Every `P0` and every `PATHOLOGICAL` id has a fixture. **Fails.**
2. Every registered fixture id exists in the catalogue. **Fails** — catches a
   fixture surviving an entry's retirement.
3. Every `FR-nn` cited by a covered scenario is defined in `SDD-VRP-001` §3.
   **Fails** — this is the dangling-reference check that produced `FR-P01`/`FR-P03`
   in v2.1, run continuously instead of once.
4. P1/P2 coverage percentage. **Reports**, never fails. P2 is *"must not be
   architecturally excluded"*; gating on it would make the catalogue unextendable.

**Acceptance:** the gate is red until Phase 1's fixtures are registered, then green
for the right reason; deleting a fixture or an entry turns it red.

**Shipped**, and one thing landed differently from the sketch above. Assertion 1
covers the adversarial set only; P0 operational coverage is a strict xfail until
Phase 3 registers those fourteen. A gate that is red for months is a gate people
learn to ignore, and the strict xfail is self-correcting — the moment Phase 3
lands it xpasses, the suite fails, and that is the signal to promote it.

Two additions the sketch did not have. `UC-072`'s subject is the matrix build
rather than a routing instance, so there is nothing for a `Problem` builder to
return; `NOT_AN_INSTANCE` names it with the reason, on the same principle as
`diagnose.UNIMPLEMENTED`. And §0.5's fourth query — "which requirements have no
scenario", a set-difference against `SDD-VRP-001` §3 — runs in both directions,
so a dangling citation and an unevidenced requirement are separate assertions
with separate messages. The unevidenced set is asserted equal to `{FR-32}`,
which is what §0.6 documents and declines to settle.

The three duplicate copies of the fifteen instances — registry, tests, example —
are now one. Perturbation-checked three ways: removing a fixture, orphaning one,
and citing a requirement the design document does not define.

---

## 5. Phase 3 — the 14 P0 scenarios (§13.1, §13.5) — **shipped**

*"P0 scenarios become seeded synthetic fixtures first — one per P0 scenario, at
three sizes."* and *"Every fixture carries its `Breaks` assertion as a named test.
A fixture asserting only 'returns a feasible solution' is not earning its
runtime."*

Follow `vrp/bench/corpus.py`'s convention: instances are **specified, not stored**
— a `Spec` plus a seed rebuilds them byte for byte. Three sizes: small (~12 stops,
fast tier), medium (~60), large (~300, marked slow).

The fourteen, with the assertion each one owes. This table is the phase:

| id | Operation | The named test |
|---|---|---|
| `UC-075` | Delivery-station sequencing | Adherence to the zone prior beats the distance-optimal sequence on the adherence metric, and the plan is still verified |
| `UC-077` | Single technician's day | Re-sequence returns sub-second at the small size — the response budget *is* the requirement |
| `UC-087` | Mid-shift re-sequence after a missed stop | The tour starts at the current position, not the depot; executed stops do not move |
| `UC-013` | Waste collection, three tips a shift | Chained single-trip plans over-spend the driver day against the multi-trip plan on the same instance |
| `UC-004` | Beverage with empties | Totals fit, peak load does not → rejected (the realistic twin of `UC-066`) |
| `UC-001` | Grocery home delivery | Fleet sized from volume is short against a fleet sized from slot overlap |
| `UC-003` | Retail receiving bay | A 20-minute-late arrival at a hard bay window is infeasible, not penalised |
| `UC-009` | Parcel last mile | A plan 8% shorter with worse access scores worse — service time dominates (§4.2) |
| `UC-019` | Utility appointments | Home-start technicians route as multi-depot; a single-depot model is worse on the same instance |
| `UC-002` | Multi-temperature replenishment | Aggregate volume fits, frozen compartment does not → rejected |
| `UC-134` | Overlapping depot catchments | Nearest-depot assignment is beaten, and stockout yields `DEPOT_STOCKOUT` not over-allocation |
| `UC-032` | Breakdown recovery | Churn against the pre-breakdown plan is bounded; passed stops immovable |
| `UC-033` | Urgent order injection | Quoted insertion cost equals realised downstream lateness, not marginal distance |
| `UC-171` | Driver absence at shift start | Stripping and redistributing beats a from-scratch re-solve on churn at equal or better cost |

Several assert *"beats the naive answer on the same instance"*, which needs the
naive answer computed. That is a helper, not a per-fixture burden, and it is what
makes these tests worth their runtime.

**Shipped.** Ten of the fourteen assert their `Breaks` line and pass. Four
turned up gaps, and three of those are one defect wearing different clothes:

`FR-10` skills, `FR-10` order-class incompatibility, `FR-11` site access and
`FR-31` depot inventory are all enforced by the independent verifier and
reported by pre-flight, and **none of them is compiled into the search**. The
PyVRP adapter's `add_vehicle_type` carries capacity, depots, shifts, costs and
reload limits, and nothing that makes a client ineligible for a vehicle. So the
engine reliably detects an illegal plan it had no way to avoid building:
`UC-019` sends gas work to an electricity-only crew, `UC-134` draws from an
empty depot, `UC-067` loads hazardous goods beside foodstuff. Closing this is
its own slice -- PyVRP supports it through per-profile edge sets and client
groups, so the work is in the adapter and the domain model, not in the search.

The fourth is separate. `UC-171`'s absence at shift start exposes that §8.4's
cheapest-insertion recovery is built for a mid-day disruption with most of the
plan committed. At shift start, where nothing is committed, it drops half the
round: re-planning the reduced fleet from scratch serves 12 of 12 and moves 6
stops, while the targeted response serves 6 of 12, moves all 12, and takes the
objective from 63,736 to 644,730. Opening more neighbours does not change it.

All four are strict xfails with the measurement in the reason, and all four
entries are `PARTIALLY_MODELLED` with the gap written down.

**This phase needs pytest markers, which do not exist today** — `pyproject.toml`
declares none and `make test` is an unfiltered `pytest tests/ -q`. Add a `slow`
marker, `-m "not slow"` to `make test`, and a `make corpus` target for the full
three sizes, following `property-soak`'s precedent of a heavy gate outside the
default run.

---

## 6. Phase 4 — one benchmark-comparable fixture per variant (§13.3)

*"so public benchmark performance and production performance can be related."*

Mostly done by `T-06`/`T-16`. `benchmarks/instances/` holds Solomon (RC208 with its
`.sol`), CVRPLIB (E-n22-k4, Uchoa X-n101) and Li & Lim (lrc206). Missing against
the catalogue's four:

- **TSPLIB** — §5 became a first-class section in v2.0 and has no benchmark anchor.
  §11.3 of the SDD does not list TSPLIB either, so this is a gap in both documents.
- **Cordeau MDVRPTW** — listed in SDD §11.3 as *"the set closest to this business's
  own shape"*, and `MDHVRPTW` is the second-largest variant here (29 scenarios).
  Not present. The SDD already flags that the instance family and BKS source need
  confirming before wiring.

Both go through `vrp/benchmarks.py`'s existing mapping and the `T-16` gate. Respect
§11.3's objective-convention rule: comparing our objective to a published BKS
under a different convention is a reporting defect, not a result.

---

## 7. Phase 5 — the requirements the catalogue found (§12.2)

Only now, because these change the SDD rather than the tests. §12.2 lists seven
recommended requirements, each *"supported by three or more scenarios"*. Ordered by
evidence weight, which is the order they should be specified in:

| Proposed | Scenarios | Id |
|---|---|---|
| Multi-period horizon with visit-frequency compliance | 7 | `FR-P01` |
| Maximum ride time per passenger or shipment | 7 | — |
| Route synchronisation (two-echelon, convoy, transhipment) | 4 | — |
| Crew as a resource distinct from the vehicle | 3 | `FR-P03` |
| Preemption of in-progress work by higher priority | 3 | — |
| Split `FR-13` into commercial priority, SLA clock, statutory obligation | 5 | — |
| Sequence-dependent service and setup time | 2 | — |

§0.6 is explicit about ownership: *"Writing one is `SDD-VRP-001`'s job, and this
table exists so somebody doing that can see who is asking and why."* So each item
is: write the `FR`, add its `T-xx` with a definition of done, add its `E-xx`
example, then build. Five of the seven have no proposed id yet; assigning them from
the reserved `FR-P02`, `FR-P04`–`FR-P10` range is part of the same edit, per §0.6.

Two smaller items belong here:

- **`FR-32` is exercised by no entry.** §0.6 records this as a catalogue gap and
  declines to settle it: *"deciding which of them tests fleet minimisation is a
  judgement about the operation."* `UC-171` and `UC-136` are the candidates.
  `UC-171` is already P0 and already in Phase 3, which makes it the cheap answer.
- **The five non-`MODELLED` entries** need their status re-confirmed rather than
  inherited: `UC-042`/`UC-174` (arc routing, *declined explicitly*), `UC-173` (IRP,
  handled upstream), `UC-045`/`UC-175` (partial). §10.5 calls this boundary
  *deliberately partial*; the corpus should assert the refusal is clean, not absent.

---

## 7a. Every phase ships an example, not only tests

A test proves the engine is right; it does not show anyone what the thing is
for. Each phase therefore ships a runnable example alongside its tests, under
`examples/src/fleet/`, and a phase is not finished until it exists.

A `UC-nnn` entry is a real operation, so the example is the *solution to that
operation*, shown running — which is a different artefact from an `E-xx` example
demonstrating a requirement.

Phase 1 shipped `examples/src/fleet/adversarial/pathological_instances.py`: all
fifteen instances, six themes, offline. Offline is a deliberate departure from
the house convention of driving examples off the Costa Rica dataset, because
§13.2 requires these instances to be "hand-built and tiny" — wiring in the
gateway would contradict the catalogue and slow the thing down.

Writing it exposed a separate defect. `examples/main.py` walked only one level
of `examples/src/`, so 31 of the 51 examples — every rich-VRP, allocation,
dynamic-dispatch and learning one — never appeared in the menu. The walk is now
recursive.

---

## 8. Order, and why it is this order

1. **Phase 1** — 15 tiny tests, no machinery, immediate defects. Also makes the
   header true.
2. **Phase 2** — the gate, once there is something for it to be green about.
3. **Phase 3** — the fourteen. The bulk of the work.
4. **Phase 4** — TSPLIB and Cordeau, independent of 1–3, can run in parallel.
5. **Phase 5** — SDD changes, last, because they are the only part that changes
   what the engine is required to do.

Phases 1–2 are small. Phase 3 is not: fourteen operations × three sizes, plus a
naive-baseline helper, plus marker infrastructure — the largest phase since `T-53`.
Phase 5 is seven requirements' worth of specification before any code.

## 9. What this deliberately does not do

- **No refactor of the twenty local `problem()` builders** in `tests/vrp/`. They
  are duplicated, and consolidating them is a separate change that would touch 53
  files and obscure every diff in this phase.
- **No anonymised production instances** (§13.4). That directive is explicitly
  conditional — *"as customers arrive"* — and there are none.
- **No L5/L6/L7 work** (frozen production corpus, shadow, canary). `T-65` shipped
  the tooling; the data is a depot and a month, which this phase cannot supply.
