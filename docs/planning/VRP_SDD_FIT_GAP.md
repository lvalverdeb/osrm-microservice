# VRP SDD — fit/gap, example catalogue, and upstream wishlist

Companion to [`../vrp-spec-driven-development.md`](../vrp-spec-driven-development.md).
That document specifies a fleet-optimisation platform. This one measures what
this workspace actually implements against it, proposes a Python example per
aspect, and lists what would have to come from upstream projects.

**Verdict up front.** Of the SDD's 29 functional requirements, **none are fully
met and four are partially met**. The gateway's `/vrp` accepts eight fields:

```
depots, stops, capacity, max_radius_km, vehicle_count, clustering_mode,
hysteresis_m, roundtrip
```

`vehicle_count` is validated and then never read. There is no way to express a
time window, a service duration, a skill, a shift, a second capacity dimension,
or a vehicle type. So the request "examples that solve each and every aspect"
cannot be satisfied against the current API — most aspects have no field to put
them in. What the examples *can* do is stated per row below, honestly tiered.

---

## 1. What exists today

| Layer | Implementation |
|---|---|
| Travel data | `osrm-routed` via `/table` (durations + distances), `/trip` for sequencing |
| Allocation | Single-pass nearest-depot `argmin` over a cost matrix, Euclidean anchor tiebreak, hysteresis band, sanity override (`gateway/src/vrp/allocate.rs`) |
| Sequencing | Stops chunked to `min(VRP_CHUNK_SIZE, capacity)`, one OSRM `/trip` per chunk, chunks fanned out concurrently (`gateway/src/vrp/solve.rs`) |
| Objective | None. There is no evaluator, no cost model, and nothing to compare two solutions with |
| Verifier | None |

This is the SDD's Layer B (matrix) and a heuristic standing in for part of
Layer D. Layers A (pre-flight), C (canonical evaluator), E (verifier),
F (explanation), G (dynamic), H (learning) do not exist.

---

## 2. Fit/gap — core model (§3.1)

Legend: **●** met · **◐** partial · **○** absent

| ID | Requirement | | Gap |
|---|---|---|---|
| FR-01 | Jobs and paired shipments | ○ | No pickup→delivery pairing, precedence, or same-vehicle constraint |
| FR-02 | Multi-dimensional capacity | ◐ | One scalar `capacity`. No weight/volume/pallet independence |
| FR-03 | Simultaneous pickup and delivery | ○ | Load is assumed monotonic; never modelled at all |
| FR-04 | Hard/soft, multiple time windows | ○ | No time dimension in the request |
| FR-05 | Service duration | ○ | Stops are instantaneous |
| FR-06 | Release times and due dates | ○ | — |
| FR-07 | Heterogeneous fleet | ○ | Vehicles are implicit and identical; capacity is global, not per vehicle |
| FR-08 | Multiple depots, per-vehicle start/end | ◐ | Multi-depot yes. No open routes, no distinct start/end |
| FR-09 | Multi-trip with reloading | ○ | One chunk = one vehicle load = one route |
| FR-10 | Skills and incompatibility | ○ | — |
| FR-11 | Site access restrictions | ○ | Not in `/vrp`. `/matrix` does expose `exclude`, so a client can approximate |
| FR-12 | Optional orders with prizes | ○ | Every stop is mandatory unless out of `max_radius_km` |
| FR-13 | Priority tiers | ○ | — |
| FR-14 | Time-dependent travel | ○ | **Blocked upstream** — see §5 |
| FR-15 | Breaks and rests (561/2006, HOS) | ○ | — |
| FR-16 | Shift windows | ○ | — |
| FR-17 | Workload balancing | ○ | Chunking bounds route *size*, which is not balance |
| FR-18 | Consistency across periods | ○ | `hysteresis_m` gives depot stability only, not driver-customer |
| FR-19 | Dock synchronisation | ○ | — |
| FR-20 | EV range and recharging | ○ | — |
| FR-21 | Route locking | ○ | No way to pin, forbid, or fix a prefix |
| FR-22 | Partial dispatch | ○ | — |

## Fit/gap — allocation (§3.2)

| ID | Requirement | | Gap |
|---|---|---|---|
| FR-30 | Operational allocation (FSM-VRP) | ○ | Vehicle count is an *output* of chunking, never a decision. `vehicle_count` is ignored |
| FR-31 | Depot allocation | ◐ | Chosen by proximity heuristic, not optimisation. No inventory awareness |
| FR-32 | Vehicle-count minimisation mode | ○ | No objective modes at all |
| FR-33 | Own vs hired capacity | ○ | — |
| FR-34 | Tactical fleet sizing | ○ | — |
| FR-35 | Territory design | ◐ | `clustering_mode` + `hysteresis_m` produce stable-ish territories as a side effect |
| FR-36 | Allocation explainability | ○ | Response has no utilisation, duty, or marginal-cost reporting |

## Fit/gap — principles and invariants

| Item | | Note |
|---|---|---|
| CON-1 feasibility over optimality | ○ | Nothing checks feasibility; there are no constraints to violate |
| CON-4 determinism | ● | Allocation is deterministic; ties break to lowest index, matching `argmin` |
| CON-5 explainability | ○ | No reasons, no unassigned-cause reporting |
| CON-9 benchmarks before opinions | ○ | No frozen corpus, no benchmark gate |
| INV-1..9 verifier invariants | ○ | No verifier exists. **Cheapest high-value gap** — see EX-30 |

---

## 3. Proposed example catalogue

Tier tells you where the work happens:

- **A** — runs against the gateway today; the API expresses it.
- **B** — the example implements the logic in Python and uses the gateway only
  for travel data (`/matrix`) and sequencing (`/trip`). This is the SDD's own
  Layer B/D split, so it is legitimate architecture, not a workaround — but be
  clear that the gateway is not solving it.
- **C** — blocked; specified here but not writable until something changes.

Per SDD §7.3, tier-B examples should use **PyVRP** or **OR-Tools** rather than
hand-rolled search. The SDD forbids a bespoke core without an ADR (§7.4).

### Already in the tree

| Example | Covers |
|---|---|
| `vrp/clustering_mode_comparison.py` | FR-31 partial — distance vs travel-time vs radial |
| `vrp/hysteresis_demo.py` | FR-18 partial, FR-35 partial — territory stability |
| `vrp/visualize_vrp.py` | CON-5 partial — visual explanation |
| `vrp/stress_test_vrp.py` | NFR scale probing |
| `clustering/run_clustering_workflow.py`, `simple_id_example.py` | FR-31, custom IDs |
| `benchmarking/compare_tsp.py` | §11.3 seed of a benchmark gate |

### Tier A — writable now, no new gateway surface

| # | Example | Covers | Purpose |
|---|---|---|---|
| EX-01 | `vrp/capacity_sweep.py` | FR-02◐ | Vary `capacity`, chart vehicles-used vs total distance — the FR-32 tradeoff, observed rather than optimised |
| EX-02 | `vrp/multi_depot_allocation.py` | FR-08◐, FR-31◐ | Same stops, 1/2/4 depots; show allocation shift and distance change |
| EX-03 | `vrp/radius_and_unreachable.py` | FR-12 inverse | `max_radius_km` as crude prize-collecting; who gets dropped and why |
| EX-04 | `vrp/open_vs_roundtrip.py` | FR-08◐ | `roundtrip` on/off; the only open-route control that exists |
| EX-05 | `vrp/determinism_check.py` | CON-4 | Same input N times, assert byte-identical plans; shuffle input order and show what changes |

### Tier B — Python solves it, gateway supplies travel data

| # | Example | Covers | Notes |
|---|---|---|---|
| EX-10 | `vrp/tw/cvrptw_ortools.py` | FR-04, FR-05, FR-16 | Reference CVRPTW: `/matrix` → OR-Tools with windows and service times. **The single most valuable example** — it is the SDD's Slice 1 |
| EX-11 | `vrp/tw/cvrptw_pyvrp.py` | FR-04, FR-17 | Same instance through PyVRP; compare quality and runtime (§7.3 portfolio) |
| EX-12 | `vrp/rich/multi_capacity.py` | FR-02, FR-03 | Weight/volume/pallets independently; simultaneous pickup and delivery |
| EX-13 | `vrp/rich/shipments.py` | FR-01 | Pickup→delivery pairing with precedence and same-vehicle |
| EX-14 | `vrp/rich/skills_and_access.py` | FR-10, FR-11 | Vehicle↔order eligibility; `/matrix` with `exclude` for restricted classes |
| EX-15 | `vrp/rich/heterogeneous_fleet.py` | FR-07, FR-33 | Per-vehicle capacity and cost; own vs hired spillover |
| EX-16 | `vrp/rich/priority_and_prizes.py` | FR-12, FR-13 | Lexicographic tiers; decline low-value work under scarcity |
| EX-17 | `vrp/rich/multi_trip.py` | FR-09 | Reload at depot mid-shift |
| EX-18 | `vrp/rich/breaks_eu561.py` | FR-15, FR-16 | Driving-hours rule set as a pluggable policy |
| EX-19 | `vrp/rich/locks_and_overrides.py` | FR-21, CON-7 | Pinned assignments, fixed prefixes, forbidden pairs |
| EX-20 | `vrp/rich/balancing.py` | FR-17 | Duration/distance/stop-count balance as a soft objective |
| EX-21 | `vrp/rich/dock_sync.py` | FR-19 | Bay capacity per slot |
| EX-22 | `vrp/alloc/fleet_mix.py` | FR-30, FR-32, FR-36 | FSM over a bounded fleet; report utilisation and marginal cost of each vehicle |
| EX-23 | `vrp/alloc/tactical_sizing.py` | FR-34 | Scenario set → recommended composition |
| EX-24 | `vrp/alloc/territories.py` | FR-35 | Balanced territories usable as a warm start |
| EX-25 | `vrp/dynamic/dispatch_waves.py` | FR-22, §8.1–8.3 | Wave loop with commit horizon and re-optimisation |
| EX-26 | `vrp/dynamic/latency_tiers.py` | §8.4, NFR | Measure the three tiers against a real matrix |
| EX-30 | `vrp/verify/independent_verifier.py` | **INV-1..9, CON-1** | Pure-Python checker: no stop served twice, capacity respected, windows honoured, totals recomputed from the matrix. Depends on nothing else and makes every other example trustworthy |
| EX-31 | `vrp/verify/canonical_evaluator.py` | §5, CON-9 | One cost function all examples score against, never the engine's own accounting |
| EX-32 | `vrp/bench/frozen_corpus.py` | §11.3, CON-9 | Generate and freeze instances; regression-gate solution quality |
| EX-33 | `vrp/learn/service_time_calibration.py` | §12.1 | Fit service time from observed dwell |
| EX-34 | `vrp/learn/speed_calibration.py` | §12.2 | Compare planned vs actual; feed back a correction factor |
| EX-35 | `vrp/explain/why_unassigned.py` | CON-5, FR-36 | Per-order reason: which constraint bound |

### Tier C — blocked

| # | Example | Covers | Blocked on |
|---|---|---|---|
| EX-40 | `vrp/rich/time_dependent.py` | FR-14 | OSRM has no departure-time parameter. See §5 |
| EX-41 | `vrp/rich/ev_recharging.py` | FR-20 | Needs charger locations and charging-curve data this stack has no source for |
| EX-42 | `vrp/rich/profile_per_vehicle.py` | FR-07 (routing side) | One `osrm-routed` serves one profile; needs several engines and gateway routing between them |

**Suggested order.** EX-30 first — a verifier makes everything after it
checkable. Then EX-10 (the CVRPTW reference), then EX-31/EX-32 to get a
benchmark gate, then the rich constraints in FR priority order.

---

## 4. What this means for the SDD

The SDD is a specification for a platform this workspace has not started. That
is not a criticism of either — but two things follow:

1. **The gateway is Layer B, not Layer D.** It supplies travel matrices and a
   TSP sequencer. If the SDD is the target, the honest framing is that `/vrp`
   is a convenience endpoint over `/table` + `/trip`, and a real solver sits
   beside it rather than inside it.
2. **`vehicle_count` is a false affordance.** It is in the published schema and
   validated, and it does nothing. Either wire it to FR-30/FR-32 or remove it;
   leaving it is how a caller comes to believe fleet sizing is supported.

---

## 5. Upstream wishlist

Things the SDD needs that **OSRM** does not provide. Ordered by how much they
block.

| # | Want | Why | Current position |
|---|---|---|---|
| U-1 | **Departure-time / time-dependent matrices** — a `departure_time` on `/table` and `/route`, with FIFO-safe speed profiles | FR-14, and the SDD's whole cost-realism argument (§5.3). Without it every plan assumes free-flow speed all day | Not supported. The v5.24 API has no time parameter on any service. Valhalla and GraphHopper both offer forms of this; OSRM's contraction hierarchy makes it hard, and the MLD pipeline is the plausible route |
| U-2 | **Multi-profile in one instance** — serve `car`/`truck`/`bike` from one process, profile selected per request | FR-07 heterogeneous fleet, FR-11 access classes. Today the profile path segment is **decorative** — `osrm-routed` ignores it (verified: `/route/v1/banana/...` returns 200) | One process, one profile, fixed at extract. Needs N engines and routing between them |
| U-3 | **Matrix sources/destinations beyond `--max-table-size`** or a streaming/chunked matrix API | NFR scale. Default cap is 100, so 10 000 cells; large instances need batching that the gateway already has to do | Configurable at launch, but memory-bound. A server-side chunked response would remove the batching from every client |
| U-4 | **Polyline-encoded coordinate input on all services** | The 24 KB URL ceiling is what limits trace and matrix size today. Polyline6 is far more compact than `lon,lat;` pairs | **Already supported by OSRM** (`polyline(...)`) — this one is a *gateway* gap, not upstream. Cheapest win on this list |
| U-5 | **Turn/step-level cost attribution** in `/table` | §5.3 cost realism — apportioning cost to legs currently means a second `/route` call | `/route` gives legs and steps; `/table` gives totals only |
| U-6 | **Access restriction as a query parameter** (height, weight, hazmat) rather than a compile-time profile class | FR-11. `exclude` handles profile-defined classes only, so every restriction combination is a separate extract | Profile-time only |

**Not upstream requests.** PyVRP, OR-Tools, VROOM and cuOpt are consumed, not
petitioned — the SDD's portfolio (§7.3) picks among them. Nothing on this list
blocks tier B; U-1 is the only item that blocks a requirement outright.

### The one to act on first

**U-4 is not upstream at all.** OSRM already accepts polyline input; the gateway
never builds it. That single change would raise the effective coordinate ceiling
well past today's ~720-breadcrumb `/match` limit, and it needs no cooperation
from anyone.
