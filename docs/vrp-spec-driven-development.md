# Vehicle Routing & Fleet Allocation Platform
## Spec-Driven Development Document

| Field | Value |
|---|---|
| Document ID | `SDD-VRP-001` |
| Version | 1.8 |
| Status | Authoritative — normative for implementation |
| Scope | Real-world vehicle routing, scheduling, and vehicle allocation |
| Supersedes | — |
| Audience | Product, OR/optimisation engineers, platform engineers, SRE, AI coding agents |

---

## 0. How to use this document

This document follows the Spec-Driven Development (SDD) discipline: the specification is the
primary artefact, and the implementation is derived from it rather than the other way round. It is
organised into the four canonical SDD layers so it can be split directly into a Spec Kit–style
repository layout:

| Section | SDD artefact | Maps to file | Answers |
|---|---|---|---|
| §1 | Constitution | `.specify/memory/constitution.md` | What is non-negotiable? |
| §2–§4 | Specification | `specs/001-vrp-core/spec.md` | What and why? |
| §5–§10 | Plan | `specs/001-vrp-core/plan.md` | How? |
| §11–§12 | Verification | `specs/001-vrp-core/contracts/` + test plan | How do we know it works? |
| §13 | Tasks | `specs/001-vrp-core/tasks.md` | In what order? |

**Normative language.** MUST / MUST NOT / SHOULD / SHOULD NOT / MAY are used per RFC 2119.
Every requirement carries a stable identifier (`CON-*`, `FR-*`, `NFR-*`, `ALG-*`, `AC-*`, `T-*`).
Identifiers are permanent; deprecated requirements are struck through, never renumbered.

**Traceability rule.** Every task in §13 MUST cite at least one `FR-*` or `NFR-*`. Every `FR-*`
MUST be covered by at least one `AC-*` acceptance criterion. Any code change that has no traceable
requirement is a specification defect, not a code defect — fix the spec first.

---

## 1. Constitution — non-negotiable principles

These principles bind every downstream decision. A plan or implementation that violates a
constitutional principle MUST be rejected at review, regardless of measured performance.

### CON-1 — Feasibility is not negotiable; optimality is
A plan that violates a hard constraint is worthless no matter how cheap it is. The system MUST
never emit a route plan claimed to be feasible without it passing an independent feasibility
checker that does not share code with the solver. Cost is a target; feasibility is a gate.

### CON-2 — The solver is the small part
The overwhelming majority of real-world routing failures are data failures: wrong geocodes, stale
travel-time matrices, mis-specified service times, capacity units that disagree between systems,
and orders that were never eligible for dispatch. Practitioner accounts of production deployments
consistently report that data quality, matrix accuracy, real-time update handling, dispatcher UX
and exception handling matter more than the choice of solver. Engineering effort MUST be allocated
accordingly: the ingestion, geo, and validation layers are first-class subsystems, not glue.

### CON-3 — Model the business, then choose the algorithm
The domain model (§4) is defined independently of any solver. Solvers are pluggable adapters
behind a stable internal problem representation. No solver-specific concept (an OR-Tools
dimension, a PyVRP `ProblemData`, a VROOM job) may leak into the domain layer.

### CON-4 — Determinism and reproducibility
Given the same input snapshot, the same solver version, the same random seed, and the same time
budget expressed in *deterministic units* (iterations or evaluations, not wall-clock), the system
MUST produce byte-identical output. Wall-clock-limited runs are permitted in production but MUST
record the deterministic iteration count actually achieved so that any run can be replayed.
All internal cost/time/distance arithmetic MUST use integers in fixed units (metres, seconds,
cost-cents). Floating-point accumulation in objective functions is prohibited.

### CON-5 — Explainability is a product requirement
Every route plan MUST be able to answer, per order: why was I assigned to this vehicle, in this
position, at this time? Every rejection MUST answer: which constraint made me infeasible, and what
would have to change? Dispatchers reject plans they cannot explain, and unexplainable plans are
silently overridden — which destroys the benefit.

### CON-6 — Trust the plan only as far as it survives contact with reality
Plan quality MUST be measured against executed reality (GPS/telematics), not against the
solver's own objective. Amazon's Last Mile Routing Research Challenge exists precisely because
experienced drivers routinely deviate from mathematically optimal sequences using tacit knowledge
about parking, access, traffic and building layout that the model does not contain. A plan
adherence metric is mandatory (§12.4), and systematic deviation is treated as a model defect.

### CON-7 — Human override is a first-class input, not a failure
Dispatchers MUST be able to lock assignments, pin sequences, forbid pairings, and force vehicle
allocations. Locked decisions become hard constraints in the next re-optimisation. The system MUST
NOT silently discard operator intent.

### CON-8 — Escalating fidelity, not big-bang
The system MUST be deliverable in vertical slices that are individually useful. Slice 1
(capacitated + time windows + single depot) MUST be in production before Slice 4 (stochastic
fleet sizing) is started. Every slice ships with its benchmark gate (§11).

### CON-9 — Benchmarks before opinions
Algorithmic claims MUST be substantiated on public benchmark sets (Solomon, Gehring & Homberger,
CVRPLIB/Uchoa, Li & Lim) *and* on a frozen corpus of anonymised production instances. "It felt
faster" is not evidence. Regression gates block merges.

### CON-10 — Cost of change beats cost of compute
Prefer a mature, maintained, well-tested open-source solver core over a bespoke metaheuristic. A
bespoke core is justified only when a documented constraint cannot be expressed in any candidate
engine and the business impact is quantified (§7.4). Compute is cheap; a proprietary metaheuristic
that only one departed engineer understood is not.

### CON-11 — A constraint the search cannot carry is refused, never assumed

> **PROPOSED — not yet in force.** §17.1 requires sign-off from engineering, operations and
> compliance before any change to this section binds. Until that is recorded in the amendment log,
> this states a practice the implementation already follows; it does not yet bind anything, and
> nothing may cite `CON-11` as authority for a review decision.

Every constraint reaches the plan through four steps, in this order, and the third is the one
that goes wrong:

1. **The domain model carries it**, in the layer that owns the invariant.
2. **The independent verifier checks it exactly**, per CON-1.
3. **The search is told whatever can be said soundly** — which is often less than the constraint,
   and sometimes nothing at all.
4. **What cannot be said is refused by name**, naming the feature and what it would take.

Step 3 is a *sound approximation or nothing*. An encoding that admits a violating plan is not a
partial implementation of the constraint; it is the constraint's absence wearing its name. Where
the engine offers no sound encoding, two answers are permitted and a third is not:

- **A loop in the orchestrator** — solve, measure, narrow, solve again — for constraints that span
  routes, which DEC-1 already places there. `vrp/depots.py` and `vrp/synchronise.py` are the shape.
- **A refusal**, naming the instance's feature and the reason.
- **Never a plan that violates it.** An engine that returns a plan breaking a constraint it was
  given has answered a different question and said nothing about the difference.

**Failure mode if omitted.** The system reliably detects illegal plans it had no way to avoid
building. That is not a safety net; it is a defect that looks like one, because every part of it
reports success: the model accepted the constraint, the search returned a plan, and only the
verifier — after the fact, and after a dispatcher has seen it — objects. `T-72` found four
constraints in exactly that state (skills, order-class incompatibility, site access, depot
inventory), `T-76` found operator locks in it as well, and none of them was a missing check. Every
one had a check. What each lacked was any way for the search to avoid the plan the check would
reject.

**Why it is constitutional rather than a plan detail.** The choice recurs for every constraint the
platform adds, the wrong answer is invisible in testing that only checks plans, and the cost lands
on operations rather than engineering. `T-72`–`T-78` each rediscovered it independently; the rule
exists so the next one does not.

---

## 2. Specification — problem statement and context

### 2.1 Business problem

An operator must serve a set of transport requests each planning cycle using a limited,
heterogeneous fleet operated by drivers with legal working-time limits, out of one or more depots,
across a road network whose travel times vary by time of day. The operator must decide:

1. **Allocation** — how many vehicles of which type to deploy, from which depot, on which shift.
2. **Assignment** — which requests go to which vehicle (and which are deferred or rejected).
3. **Sequencing** — the order of stops on each vehicle.
4. **Scheduling** — departure, arrival, service, break and rest times for each stop.
5. **Adaptation** — how to revise 1–4 as orders arrive, vehicles break down, and traffic changes.

These are not separable in practice. Fleet composition determines achievable routes; routes
determine required fleet. The system MUST treat allocation and routing as a joint decision at the
operational horizon, and as a two-stage decision at the tactical/strategic horizon (§8).

### 2.2 Why this is hard

- **Combinatorics.** The VRP is NP-hard; exact branch-price-and-cut is reliable only into the low
  hundreds of customers for constrained variants. Production instances routinely exceed this.
- **Richness.** Real instances are "rich VRPs": heterogeneous fleets, multiple depots, multi-trip,
  pickup-and-delivery, compatibility rules, working-time law, and time-dependent travel — all at
  once. Textbook variants are special cases, not the target.
- **Dynamism.** Orders arrive during the day. Committing early loses consolidation opportunity;
  committing late risks infeasibility. This is a stochastic sequential decision problem, not a
  static optimisation.
- **Soft truth.** The true objective is a mixture of cost, service, driver satisfaction, and
  consistency, only some of which is measurable. Over-fitting to distance is a classic failure.

### 2.3 Personas

| ID | Persona | Primary need |
|---|---|---|
| P1 | **Planner** (day-before) | Produce a cost-efficient, legal, executable plan for tomorrow |
| P2 | **Dispatcher** (day-of) | Absorb disruption in minutes without re-planning the world |
| P3 | **Driver** | A route that is realistic, familiar, and fairly loaded |
| P4 | **Network analyst** | Answer "what fleet should we own / lease / contract?" |
| P5 | **Integration engineer** | A stable, versioned API and predictable latency |
| P6 | **Compliance officer** | Provable adherence to driving-hours regulation |

### 2.4 User stories with acceptance criteria

**US-1 (P1) — Overnight plan.**
> As a planner, I submit tomorrow's confirmed orders and my available fleet, and receive a
> complete, feasible, costed plan within my batch window.

- `AC-1.1` Given ≤ 2,000 orders and ≤ 200 vehicles, a feasible plan is returned within 15 minutes.
- `AC-1.2` The plan passes the independent feasibility checker with zero hard violations.
- `AC-1.3` Every unassigned order carries a machine-readable rejection reason (§6.5).
- `AC-1.4` Re-running the identical request with the identical seed returns an identical plan.

**US-2 (P2) — Mid-day disruption.**
> As a dispatcher, when a vehicle breaks down at 11:00, I re-optimise only the affected and
> nearby work while everything already executed or committed stays fixed.

- `AC-2.1` A re-optimisation with 90% of stops locked returns in ≤ 30 seconds.
- `AC-2.2` No stop already visited or currently en route is moved.
- `AC-2.3` The response reports the delta versus the previous plan (stops moved, cost change,
  new lateness) rather than only the new plan.

**US-3 (P2) — Dynamic order intake.**
> As a dispatcher operating same-day delivery, new orders arriving in the current wave are either
> dispatched now or deliberately postponed to consolidate with later arrivals.

- `AC-3.1` The system classifies each open order as `must-go` (postponement makes its time window
  unreachable) or `deferrable`, and never postpones a `must-go`.
- `AC-3.2` The dispatch policy is selectable and its expected cost is reported against a
  greedy-dispatch baseline over a replayed historical day.

**US-4 (P4) — Fleet what-if.**
> As a network analyst, I evaluate fleet compositions against a demand scenario set and receive
> total-cost-of-ownership curves.

- `AC-4.1` A scenario sweep over ≥ 30 demand days and ≥ 10 candidate fleet mixes completes
  unattended and reports fixed + variable + failure cost per mix.
- `AC-4.2` Results include a service-level column (orders served within window), not cost alone.

**US-5 (P6) — Compliance evidence.**
> As a compliance officer, I obtain, for any planned duty, the driving/working/break/rest
> timeline and the rule that forced each break.

- `AC-5.1` Every planned duty exports a timeline with each break annotated by rule reference.
- `AC-5.2` Where a driver's already-consumed hours are supplied as input, planning respects the
  remaining legal envelope rather than assuming a fresh duty.

### 2.5 Out of scope (v1)

Explicitly excluded, to be revisited only via a spec amendment:

- Arc routing (street-sweeping, gritting, meter reading) — different problem class.
- Intermodal / long-haul network design with transhipment across modes.
- Crew pairing and rostering beyond single-duty legality checks.
- Real-time turn-by-turn navigation guidance (consume a navigation provider instead).
- Warehouse picking, load building beyond aggregate capacity dimensions and simple LIFO.
- Autonomous vehicle motion planning.

---

## 3. Requirements

### 3.1 Functional requirements — core model

| ID | Requirement | Priority |
|---|---|---|
| `FR-01` | Represent transport requests as either single-stop **jobs** or paired **shipments** (pickup → delivery) with precedence and same-vehicle enforcement | MUST |
| `FR-02` | Support **multi-dimensional capacity** (e.g. weight, volume, pallets, floor positions, cash) with independent limits per vehicle | MUST |
| `FR-03` | Support **pickup and delivery quantities simultaneously** on a route, so load varies non-monotonically along the route | MUST |
| `FR-04` | Support **hard and soft time windows**, and **multiple disjoint windows** per stop | MUST |
| `FR-05` | Support **service duration** modelled as fixed + per-unit-quantity + per-vehicle-type components | MUST |
| `FR-06` | Support **release times** (an order cannot depart before goods are available) and **due dates** | MUST |
| `FR-07` | Support **heterogeneous fleet**: per-vehicle capacity, fixed cost, distance cost, time cost, overtime cost, speed/routing profile, max distance, max duration | MUST |
| `FR-08` | Support **multiple depots**, and per-vehicle start/end locations that may differ from each other and from any depot (including start-from-home / end-anywhere) | MUST |
| `FR-09` | Support **multi-trip routes** with reloading at a depot or satellite mid-route | MUST |
| `FR-10` | Support **skills / compatibility**: vehicle↔order eligibility, order↔order incompatibility (e.g. hazardous with foodstuff), and driver↔site qualification | MUST |
| `FR-11` | Support **site-dependent access restrictions** (vehicle class, weight, height, emission zone, permitted hours) | MUST |
| `FR-12` | Support **optional orders with prizes** so the solver may decline low-value work when capacity is scarce (prize-collecting / team-orienteering behaviour) | MUST |
| `FR-13` | Support **priority tiers** with lexicographic protection: a higher tier is never sacrificed to improve a lower tier | MUST |
| `FR-14` | Support **time-dependent travel times** with speed profiles that satisfy the FIFO (no-passing) property | MUST |
| `FR-15` | Support **driver breaks and rests** derived from a pluggable rule set, with EU Regulation (EC) 561/2006 and US FMCSA HOS as shipped implementations | MUST |
| `FR-16` | Support **shift windows** (earliest start, latest end, max duty duration) independent of vehicle availability | MUST |
| `FR-17` | Support **workload balancing** across vehicles on duration, distance, and stop count, as a soft objective | SHOULD |
| `FR-18` | Support **consistency constraints**: driver-to-customer stability and bounded arrival-time variation across a multi-period horizon | SHOULD |
| `FR-19` | Support **depot resource synchronisation** (loading bay / dock capacity per time slot) | SHOULD |
| `FR-20` | Support **EV range and en-route recharging** with charging-time functions | COULD |
| `FR-21` | Support **route locking**: fixed prefixes, pinned order→vehicle assignments, forbidden assignments, pinned sequences | MUST |
| `FR-22` | Support **partial dispatch decisions**: mark orders as dispatched-now vs postponed-to-next-wave | MUST |
| `FR-23` | Support a **multi-period planning horizon**: recurring visits planned across several periods as one problem rather than as independent days, with per-order visit frequency, permitted-day patterns, and compliance measured against the interval rather than the day | SHOULD |
| `FR-24` | Support a **maximum ride time** between a shipment's pickup and its delivery — for passengers, time aboard; for goods, a viability or working-life clock that starts at loading and is not the delivery window | SHOULD |
| `FR-25` | **Distinguish the sources of priority.** Commercial priority, a contractual SLA clock, and a statutory obligation are separate attributes, not one tier number: they are ordered differently, they expire differently, and only one of them is negotiable. `FR-13`'s tiers remain the mechanism; this requires that what fills them is not conflated | MUST |
| `FR-26` | Support **route synchronisation**: constraints coupling two routes at a place and time — a satellite transfer in a two-echelon network, vehicles departing as a convoy, a trailer meeting a hub cut-off | SHOULD |
| `FR-27` | Support **preemption**: higher-priority work arriving mid-shift may displace planned work that has not yet been executed, with the displaced work re-planned rather than silently dropped | SHOULD |

`FR-23`–`FR-27` were written from `CAT-VRP-003` §12.2, which records how many real
operations ask for each and which ones. Each has at least three, which is the bar
that section sets — below it a proposal is an observation about one customer.
Two of its rows remain below the bar and are deliberately not requirements yet:
crew as a resource distinct from the vehicle (`FR-P03`, two scenarios) and
sequence-dependent service and setup time (`FR-P02`, two). Both are one scenario
away, and the catalogue holds the identifiers so they can be claimed without
renumbering.

### 3.2 Functional requirements — vehicle allocation

| ID | Requirement | Priority |
|---|---|---|
| `FR-30` | **Operational allocation.** Decide, per planning run, which of the available vehicles to deploy. Unused vehicles incur no fixed cost. This is the Fleet Size and Mix VRP (FSM-VRP) applied to a bounded fleet | MUST |
| `FR-31` | **Depot allocation.** Where multiple depots can serve an order, choose the depot as part of optimisation, subject to inventory availability per depot | MUST |
| `FR-32` | **Vehicle-count minimisation mode.** Support a lexicographic mode where the number of vehicles is minimised before travel cost, and a cost-mode where a vehicle is used whenever its fixed cost is repaid | MUST |
| `FR-33` | **Own vs. hired allocation.** Model third-party/contracted capacity with distinct cost structures (per-job, per-day, per-km) and allow spillover to hire when own fleet is exhausted | MUST |
| `FR-34` | **Tactical fleet sizing.** Given a scenario set of historical or generated demand days, recommend fleet composition minimising expected total cost (acquisition/lease + routing + expected failure/recourse cost) | SHOULD |
| `FR-35` | **Territory design.** Produce stable geographic territories with balanced expected workload, usable as a warm start and as a driver-consistency device | SHOULD |
| `FR-36` | **Allocation explainability.** For every deployed vehicle, report utilisation on each capacity dimension, duty time used vs available, and the marginal cost of removing it | MUST |

### 3.3 Non-functional requirements

| ID | Requirement |
|---|---|
| `NFR-01` | **Batch latency.** ≤ 2,000 stops / ≤ 200 vehicles: feasible plan ≤ 15 min. ≤ 10,000 stops: ≤ 60 min via decomposition (§7.6) |
| `NFR-02` | **Interactive latency.** Single-order insertion / removal quote: p95 ≤ 2 s. Locked re-optimisation of one region: p95 ≤ 30 s |
| `NFR-03` | **Anytime behaviour.** The solver MUST hold a best-known feasible incumbent from the first construction onward and return it on any timeout or cancellation |
| `NFR-04` | **Graceful degradation.** If the matrix provider is unavailable, fall back to a cached matrix and mark the plan `DEGRADED`; never fall back silently to haversine for a committed plan |
| `NFR-05` | **Horizontal scale.** Independent planning runs are isolated and parallelisable; a single run may use bounded intra-run parallelism (§7.7) |
| `NFR-06` | **Observability.** Every run emits: objective trajectory over time, incumbent timestamps, constraint-violation counts, matrix cache hit rate, seed, solver version, deterministic iteration count |
| `NFR-07` | **Data protection.** Customer addresses and driver identities are PII. Benchmark corpora MUST be anonymised and coordinate-obfuscated before leaving the production boundary |
| `NFR-08` | **Auditability.** Input snapshot, solver configuration, and output plan are immutable and retained for the regulatory retention period; a plan is replayable from its snapshot |
| `NFR-09` | **Portability.** The optimisation core MUST run on commodity CPU. GPU acceleration MAY be an optional accelerator profile, never a hard dependency |
| `NFR-10` | **Versioned contracts.** The public API is versioned; breaking changes require a new major version and a deprecation window |

---

### 3.4 Problem variants covered

The requirements above are stated as capabilities, which is the right way to
build them but the wrong way to check coverage: the literature, the benchmark
sets and most stakeholders speak in named problem classes. This maps one to the
other. Each row is the composition of requirements already specified — no row
introduces a new one.

| Variant | Composed of | Benchmark set (§11.3) | Delivered by |
|---|---|---|---|
| **TSP** — travelling salesman | One vehicle, no capacity or window binding. The degenerate case of every row below | — (exercised through CVRP with a single vehicle) | Already served today, see below |
| **CVRP** — capacitated VRP | `FR-02` capacity, `FR-07` fleet, `FR-08` depot | CVRPLIB / Uchoa | Slice 1, `T-12` |
| **VRPTW** — CVRP with time windows | CVRP plus `FR-04` windows, `FR-05` service duration, `FR-16` shift windows | Solomon, Gehring & Homberger | Slice 1 `T-12`, Slice 2 `T-23` |
| **MDHVRPTW** — multi-depot heterogeneous VRPTW | VRPTW plus `FR-07` per-vehicle capacity, cost and profile, and `FR-08` multiple depots with per-vehicle start and end | Cordeau MDVRPTW (to be verified when wired in) | Slice 2, `T-21` |
| **PDPTW** — pickup and delivery with time windows | VRPTW plus `FR-01` shipments with precedence and same-vehicle, `FR-03` simultaneous pickup and delivery | Li & Lim | Slice 1 `T-13`, Slice 2 `T-20` |

**TSP is not a separate workstream.** A single uncapacitated vehicle with
unbounded windows *is* a TSP, and the platform serves it by construction. It is
called out here for two reasons. First, so that a request for "just sequence
these stops" is recognised as this system's degenerate case rather than a
different product. Second, because it is the one variant already in production:
the existing gateway's `/vrp` delegates per-vehicle sequencing to OSRM's `/trip`
service, which is a TSP solver, and `T-39` keeps a TSP-with-time-windows polish
step for exactly this reason (§7.5).

**MDHVRPTW is the target shape for this business.** Six depots, mixed vehicles,
and customer windows is not an exotic combination — it is the ordinary case
described in §2.1, and it is why `T-21` sits early in Slice 2 rather than being
deferred as an enrichment. Anything that treats the fleet as homogeneous or the
depot as singular is a stepping stone, not a deliverable.

**Beyond these five.** The requirements also compose into variants this section
does not name — prize-collecting and team-orienteering (`FR-12`), multi-trip
(`FR-09`), consistent VRP (`FR-18`), fleet size and mix (`FR-30`), and the
time-dependent and dynamic forms (`FR-14`, `FR-22`). They are specified in §6
and §8. The five above are named because they are the classes with public
benchmark sets, and therefore the ones whose quality can be argued about with
evidence rather than assertion.

---


## 4. Domain model (canonical, solver-independent)

All quantities are integers in fixed units: distance in metres, time in seconds, cost in cents,
capacity in per-dimension integer units. `Instant` is epoch seconds; `Duration` is seconds.

### 4.1 Entities

```
Location
  id, lat, lon
  matrix_index            # position in the travel matrix
  access: AccessProfile   # vehicle classes, weight/height limits, permitted hours
  dwell_overhead: Duration# parking/walking overhead independent of the order

TimeWindow
  start: Instant, end: Instant
  hardness: HARD | SOFT
  earliness_cost_per_sec, lateness_cost_per_sec   # SOFT only

Order
  id, external_ref
  kind: JOB | SHIPMENT
  pickup:  StopSpec?      # null for delivery-only jobs
  delivery: StopSpec?     # null for pickup-only jobs
  quantities: map<dimension, int>          # signed: load applied at pickup, released at delivery
  priority_tier: int                       # 0 = must-serve; higher = more droppable
  prize: int                               # value of serving; used in prize-collecting mode
  release_time: Instant
  required_skills: set<SkillId>
  incompatible_with: set<OrderId | OrderClass>
  group_id: GroupId?                       # mutually-exclusive delivery options

StopSpec
  location_id
  time_windows: [TimeWindow]               # disjoint, sorted
  service_fixed: Duration
  service_per_unit: map<dimension, Duration>
  vehicle_type_service_factor: map<VehicleTypeId, permille>

VehicleType
  id
  capacities: map<dimension, int>
  routing_profile: ProfileId               # selects the travel matrix
  fixed_cost, cost_per_metre, cost_per_second, overtime_cost_per_second
  access_class                             # matched against Location.access
  skills: set<SkillId>
  max_distance: int?, max_duration: Duration?
  reload_allowed: bool, reload_duration: Duration

Vehicle
  id, type_id, depot_id
  start_location_id, end_location_id       # may be null => "start/end anywhere"
  shift: TimeWindow                        # availability envelope
  driver_id?
  initial_state: DriverState?              # hours already consumed today (see §6.4)
  available: bool

Break
  id, vehicle_id
  earliest: Instant, latest: Instant, duration: Duration
  placement: AT_LOCATION | ANYWHERE | AT_FACILITY(set<LocationId>)
  rule_ref: string                         # e.g. "EC-561/2006 Art.7"

Fleet         = [Vehicle]
Depot         = { id, location_id, dock_capacity_profile, inventory_by_dimension }
```

### 4.2 Problem and solution

```
Problem
  id, created_at, horizon: TimeWindow
  orders: [Order]
  fleet: Fleet
  depots: [Depot]
  matrices: map<ProfileId, TravelMatrixRef>
  objective: ObjectiveSpec                 # §5
  rules: RuleSetRef                        # driving-hours ruleset id + version
  locks: [Lock]                            # §6.6
  options: SolverOptions                   # seed, budget, mode

Solution
  problem_id, solver, solver_version, seed, iterations, wall_ms
  routes: [Route]
  unassigned: [{ order_id, reason_code, explanation }]
  objective_breakdown: map<component, int>
  feasibility: { checked_by, hard_violations: [Violation], soft_violations: [Violation] }
  status: OPTIMAL | FEASIBLE | FEASIBLE_DEGRADED | INFEASIBLE | TIMEOUT

Route
  vehicle_id
  steps: [Step]                            # START, PICKUP, DELIVERY, BREAK, RELOAD, END
  metrics: { distance, duration, driving, service, waiting, break_time,
             load_peak: map<dimension,int>, cost_breakdown }

Step
  type, order_id?, location_id
  arrival: Instant, start_service: Instant, departure: Instant
  waiting: Duration, load_after: map<dimension,int>
  violated: [ViolationCode]
```

### 4.3 Invariants (checked by the independent verifier, §11.2)

- `INV-1` Every order appears exactly once across `routes` ∪ `unassigned`.
- `INV-2` For a `SHIPMENT`, pickup and delivery are on the same route, pickup strictly before delivery.
- `INV-3` For every step, `arrival ≤ start_service`, `start_service + service = departure`, and
  `start_service` lies inside one of the stop's windows (hard windows) or the violation is recorded.
- `INV-4` `arrival(i+1) = departure(i) + travel(profile, loc(i), loc(i+1), departure(i))` where
  `travel` is evaluated against the **exact matrix version pinned in the problem**.
- `INV-5` For every dimension, `0 ≤ load_after(step) ≤ capacity(dimension)` at every step.
- `INV-6` Route duration ≤ `max_duration`; route distance ≤ `max_distance`; route lies within the
  vehicle shift window.
- `INV-7` The driving-hours timeline satisfies the active rule set, evaluated by the rules engine
  independently of the solver.
- `INV-8` All locks (§6.6) are satisfied exactly.
- `INV-9` `objective_breakdown` recomputed from `routes` equals the solver-reported objective.
- `INV-10` No route carries an order whose `required_skills` its vehicle lacks, whose `order_class`
  another order aboard forbids, or whose site refuses that vehicle's access class or weight
  (`FR-10`, `FR-11`).
- `INV-11` Reloads happen only at a vehicle's permitted reload locations, and no more often than
  `max_reloads` (`FR-09`).
- `INV-12` No depot dispatches more vehicles in a time slot than it has bays (`FR-19`).
- `INV-13` No depot supplies more of a dimension than it holds, counted **globally** across every
  route drawing on it, never per cluster (`FR-31`, `DEC-1`).
- `INV-14` No shipment is aboard longer than its `max_ride_time`, measured from departing the
  pickup to arriving at the delivery (`FR-24`).
- `INV-15` Two coupled routes meet as their `Synchronisation` requires, on different vehicles
- `INV-16` An electric vehicle never arrives anywhere past empty, and charges only where its own `charger_locations` say there is a charger
  (`FR-26`).

**`INV-10`–`INV-16` are numbered past §4.3's original nine deliberately.** Each was added when a
requirement arrived that none of the first nine covered, and each covers a plan that satisfied
every invariant this section named while being one nobody could drive. They are listed here rather
than only in `vrp/verify/verifier.py` because a specification that understates what the system
checks invites somebody to rely on a guarantee it does not know it makes.

**INV-9 is the single most valuable test in the system.** Most silent optimisation bugs are
objective-evaluation drift between the incremental move evaluator and ground truth.

---

## 5. Objective specification

### 5.1 The objective is hierarchical, not a weighted sum

Naïve weighted sums are the most common modelling error in production routing. Weights that
balance correctly on a 200-stop day silently invert on a 2,000-stop day, and business
stakeholders cannot reason about them. The system MUST implement a **lexicographic hierarchy with
bounded scaling**:

```
Tier 0  Hard-constraint violations                    (must be zero in a FEASIBLE solution)
Tier 1  Unserved priority-0 orders                    (penalty P0 per order)
Tier 2  Unserved orders by descending priority tier   (penalty = prize forgone)
Tier 3  Fleet cost:  Σ fixed_cost(v) over deployed vehicles
Tier 4  Operating cost: Σ (cost_per_metre·distance + cost_per_second·duration + overtime)
Tier 5  Soft violations: earliness/lateness/soft-capacity penalties
Tier 6  Quality tie-breakers: workload imbalance, consistency deviation, route compactness
```

Implementation: lexicographic ordering is realised by scaling, with each tier's weight chosen to
strictly dominate the maximum attainable value of all lower tiers — computed from the instance,
not hard-coded — or by staged optimisation (solve tier-by-tier, fixing the achieved value of
higher tiers as a constraint). Both approaches MUST be available; staged is preferred above
10,000 stops where scaled weights risk integer overflow.

### 5.2 Objective modes

A mode does not reorder the hierarchy. It changes **which tiers share a level**.
Tiers on separate levels are strictly ordered — no quantity of a lower tier buys any of a higher
one. Tiers sharing a level are traded against each other in a single currency. §5.1's list is the
default arrangement, and each mode below departs from it in exactly one place.

Notation: `>` separates levels and means strict domination; `+` joins tiers sharing a level.

| Mode | Levels | Use |
|---|---|---|
| `MIN_VEHICLES` | `T2 > T3 > T4` — vehicle count strictly dominates distance | Fleet-constrained days, capacity planning |
| `MIN_COST` | `T2 > T3+T4` — a vehicle is deployed iff its fixed cost is repaid by savings, which is a trade rather than a precedence | Normal operations |
| `MIN_DURATION` | As `MIN_COST`, scoring `cost_per_second` only; distance ignored | Driver-hour-constrained operations |
| `MAX_SERVICE` | As `MIN_COST`. Tier 2 already dominates Tiers 3–4 in every mode but `PRIZE_COLLECTING`, so what distinguishes this one is that orders stay **required** rather than droppable — a property of the order, not of the objective | Peak days, SLA protection |
| `PRIZE_COLLECTING` | `T1 > T2+T3+T4` — maximise Σ prizes − cost. Total prize is a constant of the instance, so this is minimising forgone prize + cost in one currency. Tier 1 stays above: a priority-0 order is a promise, not a bid | Capacity-scarce, marketplace models |

The two rows worth reading twice are `MAX_SERVICE`, which changes nothing about the tiers, and
`PRIZE_COLLECTING`, which is the only mode where Tier 2 does **not** dominate cost. An
implementation that treats every mode as strictly lexicographic will pass most tests and still be
wrong in both places: `MIN_COST` collapses into `MIN_VEHICLES` because one fewer vehicle always
wins, and `PRIZE_COLLECTING` can never drop anything.

### 5.3 Cost realism requirements

- `OBJ-1` Distance cost MUST use the routing profile's real network distance, never great-circle.
- `OBJ-2` Duration cost MUST include service and waiting time when the driver is paid for it, and
  MUST exclude waiting from *distance-based* competition metrics so benchmark comparisons remain
  valid (the DIMACS/Gehring–Homberger convention measures pure driving duration).
- `OBJ-3` Overtime MUST be modelled as a distinct piecewise cost above the paid shift, not folded
  into the linear time rate — otherwise the solver treats the first and the tenth overtime minute
  identically.
- `OBJ-4` Where the operator's true cost is a step function (a hired vehicle costs a full day),
  it MUST be modelled as a step function, not amortised per kilometre.

---

## 6. Constraint engineering — the real-world catalogue

This section is the heart of the specification. Each constraint states the business reality, the
model, and the failure mode when it is omitted.

### 6.1 Capacity

**Reality.** A van is full when *any* of weight, volume, pallet positions, cage count, or
temperature-compartment volume is exhausted. Loads are also released and picked up mid-route.

**Model.** Multi-dimensional signed load with a per-dimension running maximum. For simultaneous
pickup-and-delivery, the binding quantity is the **peak load along the route**, not the total —
computing feasibility from route totals is wrong and is a classic production bug.

**Compartments.** Where a vehicle has separated compartments (frozen/chilled/ambient), model each
compartment as its own dimension and forbid cross-assignment via order class → dimension mapping.

**Failure mode if omitted.** Plans that are physically unloadable; drivers rebuild the plan at the
dock; the optimisation is discarded.

### 6.2 Time windows and service time

**Reality.** Windows come from customer contracts, receiving-bay hours, and residential
preference. Some are hard (a retail dock closes), some soft (a home delivery). Service time varies
by drop size, vehicle type (tail-lift vs manual), and access difficulty.

**Model.** Multiple disjoint windows per stop, each hard or soft with asymmetric earliness /
lateness costs. Waiting is permitted (arrive early, wait) and MUST be costed explicitly, because
uncosted waiting produces plans that look cheap and consume the whole driver day.

**Critical engineering note.** Service time is not a rounding error. In dense urban last-mile,
travel time between stops is a minority of the driver's day; parking and walking dominate. Analysis
of the Amazon challenge data reports travel time comprising roughly a third of drivers' time, with
the balance spent parking and walking to make deliveries. A model with accurate matrices and
guessed service times will be worse than one with approximate matrices and calibrated service
times. Service-time calibration from telematics is therefore a mandatory workstream (§13, `T-62`).

### 6.3 Time-dependent travel

**Reality.** Peak-hour travel times can be 1.5–2× off-peak on the same arc. A plan built on
average speeds sends vehicles into congestion and is systematically late in the afternoon.

**Model.** Per-arc (or per-zone) piecewise-constant **speed** profiles over time buckets, which
induce piecewise-linear travel-time functions. The model MUST satisfy the **FIFO / no-passing
property**: departing later can never mean arriving earlier. Piecewise-constant *travel time* per
bucket violates FIFO and MUST NOT be used; the Ichoua–Gendreau–Potvin construction (speed changes
when a bucket boundary is crossed mid-arc) is the required formulation.

**Implementation.** Store `T` time buckets (typically 15–60 min) per profile. Travel evaluation is
`travel(i, j, departure_time)`, walking bucket boundaries. Cache per-arc breakpoints. Feasibility
checks become departure-time dependent, which forbids some O(1) concatenation shortcuts — see §7.5
for the mitigation.

**Failure mode if omitted.** Chronic afternoon lateness, dispatcher distrust, drivers padding
service times to compensate.

### 6.4 Driving hours, breaks, and rest

**Reality.** Working-time law is a hard legal constraint with criminal and licensing consequences,
and it materially changes route structure — a break is an intermediate stop that consumes time and
must be placed somewhere legal.

**EU — Regulation (EC) No 561/2006 (shipped rule set `EU-561`).** Core rules the engine must model:

- Daily driving limit 9 h, extendable to 10 h no more than twice per week.
- A break of at least 45 min after at most 4.5 h of accumulated driving; the break may be split
  into a first period of at least 15 min followed by a period of at least 30 min.
- Daily rest of at least 11 h, reducible to 9 h a limited number of times per week.
- Weekly driving limit 56 h; fortnightly limit 90 h.

Related working-time provisions (Directive 2002/15/EC) govern *working* as distinct from *driving*
time and MUST be modelled as a separate accumulator.

**US — FMCSA HOS (shipped rule set `US-HOS`).** Property-carrying baseline: 11 h driving within a
14 h duty window after 10 consecutive hours off duty; a 30-minute interruption of driving required
after 8 cumulative hours of driving; 60/70-hour limits over 7/8 days.

**Model.** A pluggable `HoursOfServiceRules` interface exposing:

```
init_state(carry_over: DriverState?) -> DriverState   # Vehicle.initial_state, below
can_drive(state, seconds) -> bool                     # may they drive this long *now*?
advance(state, activity, seconds) -> DriverState      # DRIVE | WORK | BREAK | REST | WAIT
required_break(state) -> Break?                       # what must happen next, and why
remaining_drive(state) -> Duration                    # left in the duty, breaks allowed for
drive_until_break(state) -> Duration                  # left before a break falls due
```

The last two are distinct questions and MUST NOT be collapsed into one accessor. "How much more
can this driver do today" is bounded by the daily driving and duty limits; "how much before they
must stop for 45 minutes" is bounded by the break interval. A single `remaining_drive` that
returns the minimum of both answers the second question while appearing to answer the first, so a
rested driver reports 4.5 h rather than 9 h — which reads downstream as a fleet a third smaller
than it is. `can_drive` is the break-interval question, because that is what a scheduler asks
before committing to a leg.

`init_state` takes the carry-over directly rather than looking it up by driver and date: the
authoritative value is `Vehicle.initial_state` (below), so a lookup here would be a second source
for the same fact.

Break insertion MUST be solved as a **scheduling subproblem inside route evaluation**, not as a
post-processing pass. Post-hoc break insertion produces routes that were feasible before breaks
and infeasible after — the classic symptom is a plan that "loses" its last two stops per route on
publication. The literature treats this as the truck driver scheduling problem embedded in route
evaluation, and dynamic-programming / labelling formulations over resource-extension functions are
the standard approach.

**Mandatory input.** `Vehicle.initial_state` — hours already consumed. Planning a fresh 9-hour
duty for a driver who already drove 6 hours is a compliance incident, not an optimisation gap.
Where tachograph or ELD data is available, it is the authoritative source for `initial_state`.

**Placement.** Breaks cannot always be taken at a customer. `Break.placement` distinguishes
"anywhere on an arc", "at a customer location", and "at a qualifying facility" (rest area, depot).
The last requires facility candidates in the matrix, which affects matrix sizing (§7.2).

### 6.5 Compatibility, skills, and access

- **Vehicle↔order.** Order requires refrigeration / tail-lift / ADR certification / crane.
- **Order↔order.** Foodstuff must not share a compartment with hazardous goods; competing
  retailers may forbid co-loading.
- **Driver↔site.** Site induction, security clearance, language.
- **Vehicle↔site.** Weight and height limits, urban low-emission zone class, permitted delivery
  hours for large vehicles, bridge and width restrictions.

**Model.** Bitset skill matching evaluated in O(1) during move evaluation. Order↔order
incompatibility is a route-level predicate; naive pairwise checking is O(n²) per move and MUST be
implemented as incremental class-count tracking per route.

**Rejection reason codes** (`FR-01`, `AC-1.3`) — the closed vocabulary emitted for unassigned orders:

```
NO_ELIGIBLE_VEHICLE      no vehicle has the required skills / access class
CAPACITY_EXCEEDED        order alone exceeds every eligible vehicle's capacity
TIME_WINDOW_UNREACHABLE  no eligible vehicle can arrive within any window
RELEASE_AFTER_WINDOW     goods available only after the last window closes
DUTY_LIMIT               serving it cannot fit any legal duty
INCOMPATIBLE_ONLY        eligible only with orders it is incompatible with
FLEET_EXHAUSTED          feasible but no capacity remained at this priority
DROPPED_BY_PRIZE         prize below marginal cost in prize-collecting mode
LOCK_CONFLICT            operator lock made assignment impossible
DEPOT_STOCKOUT           no depot with inventory can serve it in window
```

Each reason MUST be produced by an explicit diagnostic pass (§7.9), not inferred.

### 6.6 Locks and operator intent

```
Lock kinds:
  PIN_ORDER_TO_VEHICLE(order_id, vehicle_id)
  FORBID_ORDER_ON_VEHICLE(order_id, vehicle_id)
  FIX_ROUTE_PREFIX(vehicle_id, [order_id...])      # already executed / en route
  FIX_SEQUENCE(vehicle_id, [order_id...])          # relative order preserved
  FORCE_DEPLOY(vehicle_id) / FORBID_DEPLOY(vehicle_id)
  PIN_DEPOT(order_id, depot_id)
  FREEZE_UNTIL(instant)                            # nothing before this instant may change
```

Locks are hard constraints. If locks make the instance infeasible, the system MUST return
`INFEASIBLE` with the minimal conflicting lock set (an IIS-style diagnosis), never silently drop a
lock.

### 6.7 Consistency and fairness

**Reality.** Drivers who serve the same territory daily accumulate tacit knowledge — access codes,
parking, receiving-bay habits — which reduces service time and errors. Customers value being
served at a predictable time by a familiar driver. Consistency is a genuine cost saver, not a
concession, and it is the operational rationale behind fixed-territory models used by large
parcel carriers.

**Model.**
- *Driver consistency*: bound the number of distinct drivers serving a customer over a horizon
  (generalised ConVRP formulation), or pin via territory.
- *Arrival-time consistency*: penalise the spread `max(arrival) − min(arrival)` across the horizon
  for each customer; permit **departure-time adjustment** at the depot as a cheap lever to align
  arrival times without changing sequences.
- *Workload fairness*: penalise the spread of route duration / distance / stop count.

**Cost.** Consistency is Tier 6 by default. It MUST be measurable: report the cost delta of
enforcing consistency versus the unconstrained optimum so the business can price it.

### 6.8 Multi-trip and reloading

A vehicle that empties before its shift ends should return, reload, and go again — common in urban
distribution and waste collection. Model reload as an optional intermediate depot visit that resets
load to zero (or to a newly loaded state) at a cost of `reload_duration` plus dock queueing.
Combined with driving-hours rules this becomes a multi-trip VRPTW with an embedded driver
scheduling problem; it MUST NOT be approximated by chaining independent single-trip plans, which
double-counts driver availability.

### 6.9 Dock and resource synchronisation

Depot loading bays are finite. If 40 vehicles are planned to depart at 06:00 and there are 8 bays,
the plan is fiction. Model dock capacity as a cumulative resource over time buckets at each depot,
and stagger departures. Where reloading (§6.8) is enabled, mid-day dock contention is usually the
binding constraint, not vehicle capacity.

---

## 7. Plan — architecture and algorithms

### 7.1 System decomposition

```
┌─────────────────────────────────────────────────────────────────────┐
│  A. Intake & Validation      orders, fleet, calendars, locks         │
│     - schema validation, unit normalisation, referential integrity   │
│     - geocoding + address→road-network snapping + confidence scoring │
│     - pre-flight infeasibility diagnosis (§7.9)                      │
├─────────────────────────────────────────────────────────────────────┤
│  B. Geospatial & Matrix      OSRM / Valhalla / commercial provider   │
│     - per-profile matrices, time-dependent buckets, sparsification   │
│     - versioned, content-addressed, cached                           │
├─────────────────────────────────────────────────────────────────────┤
│  C. Model Compiler           domain Problem → solver-native model    │
│     - objective scaling, rule-set binding, lock translation          │
├─────────────────────────────────────────────────────────────────────┤
│  D. Solver Portfolio         PyVRP | OR-Tools | VROOM | custom LNS   │
│     - decomposition orchestrator for large instances                 │
│     - portfolio runner with shared incumbent pool                    │
├─────────────────────────────────────────────────────────────────────┤
│  E. Verification             independent feasibility checker         │
│     - hard/soft violation report, INV-1..INV-9                       │
├─────────────────────────────────────────────────────────────────────┤
│  F. Explanation              per-order rationale, marginal costs     │
├─────────────────────────────────────────────────────────────────────┤
│  G. Dispatch & Execution     wave controller, locks, telematics feed │
│     - dynamic dispatch policy, re-optimisation triggers              │
├─────────────────────────────────────────────────────────────────────┤
│  H. Learning & Calibration   service times, speeds, plan adherence   │
└─────────────────────────────────────────────────────────────────────┘
```

Layers A, B, E, F and H are where the durable value lives (CON-2). Layer D is replaceable.

### 7.2 Travel matrix subsystem (Layer B)

This subsystem fails more often than the solver and MUST be specified precisely.

**Providers.** Self-hosted OSRM or Valhalla over OpenStreetMap extracts are the reference
implementations; both expose a matrix/table endpoint alongside route geometry, and Valhalla
additionally provides isochrones and elevation. A commercial provider adapter is permitted behind
the same interface. VROOM's design — where the engine defaults to OSRM for both the cost matrix
and the final route geometry, and accepts a custom matrix from any source — is the pattern to
follow: **the matrix is an input, not a solver responsibility.**

**Requirements.**

| ID | Requirement |
|---|---|
| `MTX-1` | Matrices are **per routing profile** (car / van / truck-with-weight-class / bike / foot) |
| `MTX-2` | Matrices are **asymmetric**; one-way systems and turn restrictions make `d(i,j) ≠ d(j,i)` |
| `MTX-3` | Both **duration and distance** matrices MUST be retrieved; costing needs both |
| `MTX-4` | Every location MUST be **snapped** to the network with a recorded snap distance; snaps beyond a threshold raise a data-quality warning, not a silent success |
| `MTX-5` | **Unreachable pairs** MUST be represented explicitly (sentinel, not a large finite number) and handled as hard-infeasible arcs. Large-finite sentinels get "optimised into" solutions |
| `MTX-6` | Matrices are **content-addressed and versioned** (`hash(locations, profile, osm_extract_version, bucket_scheme)`); a plan pins its matrix version (INV-4, CON-4) |
| `MTX-7` | Request **chunking**: n² growth is the practical scaling wall. 5,000 locations ⇒ 25M cells. OSRM's `--max-table-size` and Valhalla equivalents MUST be configured, and requests tiled |
| `MTX-8` | **Sparsification** for large instances: retain the k-nearest neighbours per node (k ≈ 20–50) plus depot/facility rows in full. Dense matrices above ~20k nodes are memory-prohibitive and unnecessary — local search only ever evaluates near neighbours |
| `MTX-9` | **Time-dependent mode** stores `T` buckets per profile; storage is `O(nnz · T)` and MUST be built on the sparsified graph |

> **`MTX-9`'s `O(nnz · T)` is per *arc* and nothing builds it.** `T-83` carries profiles per arc *class* — `O(C · T)`, with the class derived from the arc's free-flow duration rather than stored. Per-arc profiles are not estimable from traces: §12.2 fits against observed traversals, and an individual arc is driven far too rarely to support one. The larger storage stays specified because a source of per-arc profiles that is not telematics — a published traffic model, say — would need it.

| `MTX-10` | **Cache** by location-pair and profile with LRU + persistent store; report hit rate (NFR-06). Incremental days reuse ≥ 90% of pairs in stable operations |
| `MTX-11` | A **haversine fallback** exists for development and infeasibility triage only, and MUST mark the plan `DEGRADED` (NFR-04) |

**Sizing guidance.** Budget matrix build time explicitly; for large daily plans it commonly exceeds
solve time. Pre-warm overnight from the customer master rather than on demand.

### 7.3 Solver portfolio (Layer D)

The system MUST NOT be built on a single solver. Different instance shapes have different winners,
and a portfolio with a shared incumbent pool is both more robust and trivially parallel.

| Engine | Core method | Strengths | Constraints in scope | Role here |
|---|---|---|---|---|
| **PyVRP** | Hybrid genetic search / ILS with an aggressive local search, C++ core with a Python API | State-of-the-art CVRP/VRPTW quality; the algorithm lineage ranked first in the 2021 DIMACS VRPTW challenge and first on the static track of the EURO Meets NeurIPS 2022 competition; MIT-licensed | Pickup & delivery, backhaul, heterogeneous fleet, site-dependence, time windows, release times, multi-depot, reloading/multi-trip, prize-collecting, mutually-exclusive client groups, multiple time windows | **Primary** for ≤ ~2k-stop subproblems |
| **Google OR-Tools routing** | CP over a routing model + first-solution heuristics + guided local search / tabu, with an optional CP-SAT backend | Extremely expressive constraint modelling (capacity dimensions, breaks, pickup-delivery precedence, incompatible shipments, similarity to a previous solution); mature multi-language APIs; explicitly aimed at large-scale industrial routing with complex constraints | Anything expressible as dimensions and side constraints | **Primary** where a constraint is not expressible in PyVRP; reference implementation for exotic rules |
| **VROOM** | Fast heuristics + local search, C++ | Millisecond-class solutions for moderate instances; first-class OSRM / openrouteservice / Valhalla integration; HTTP service deployment; also has an ETA-only mode that costs a *given* route and reports violations | CVRP, VRPTW, multi-depot heterogeneous, PDPTW | **Interactive tier**: quotes, insertion pricing, dispatcher what-ifs, warm starts |
| **Custom LNS core** (SISR / ALNS) | Ruin-and-recreate with adjacent string removal and greedy insertion with blinks, simulated-annealing acceptance | Simple, fast, adaptable; strong on large instances; naturally suited to dynamic insertion; includes a fleet-minimisation procedure with an absence-based acceptance criterion | Whatever you implement | **Escape hatch** for constraints no engine supports; **dynamic** re-optimisation |
| **NVIDIA cuOpt** | GPU-parallel heuristics | Very large instances at low latency where GPUs are available; reported order-of-magnitude speedups on large routing workloads | Capacity, time windows, shifts and breaks, PDP | **Optional accelerator profile** (NFR-09: never a hard dependency) |
| **CP-SAT / MILP** | Exact | Small instances; lower bounds; certifying benchmark optimality; fleet-sizing master problems | Anything modellable | **Validation & tactical**, not operational routing |

**Portfolio protocol.**

1. Run `k` configurations concurrently (different engines, seeds, first-solution strategies).
2. Every configuration publishes improved incumbents to a shared pool with the objective computed
   by the **canonical evaluator**, never by the engine's own accounting.
3. Configurations may consume pool incumbents as warm starts at restart boundaries.
4. On budget exhaustion, return the pool best that passes the independent verifier (Layer E).
5. Record per-configuration win rates by instance signature; use them to bias future allocation.

This mirrors standard practice with single-threaded routing cores: rather than seeking intra-solver
parallelism, run multiple solver instances with different first-solution and metaheuristic
strategies over the same problem.

### 7.4 Choosing to write a custom core (CON-10 gate)

A bespoke metaheuristic is authorised only when **all** of the following hold, documented in an ADR:

1. A hard constraint cannot be expressed in PyVRP or OR-Tools without a hack that breaks INV-9.
2. The constraint's business impact is quantified (€ / service points) over the frozen corpus.
3. The proposed core is a documented published method (e.g. SISR, ALNS with named operators), not
   an invention, so that behaviour is reviewable and reproducible.
4. It ships with the same benchmark gates as the incumbent engines (§11).

### 7.5 Algorithmic specification

#### ALG-1 — Construction
Parallel cheapest insertion with regret-`k` (k = 2 or 3) as default; savings (Clarke–Wright) and
nearest-neighbour retained for portfolio diversity. Insertion order MUST be randomised across
portfolio members. Where an engine supplies first-solution strategies natively (OR-Tools'
`PATH_CHEAPEST_ARC`, `PARALLEL_CHEAPEST_INSERTION`, `AUTOMATIC`), the portfolio MUST sample across
them rather than fixing one — reported best-performing strategies vary by instance family.

#### ALG-2 — Local search neighbourhoods
Minimum required move set, evaluated on a **granular neighbourhood** (each node linked only to its
`k` nearest eligible neighbours, k ≈ 20–40, plus depot):

- `relocate(1..3)` — move a segment of 1–3 consecutive nodes, intra- and inter-route
- `swap(1..3, 1..3)` — exchange segments
- `2-opt` (intra-route), `2-opt*` (inter-route tail exchange)
- `or-opt` — segment relocation preserving orientation
- `swap*` — exchange two nodes between routes **without** requiring insertion at the vacated
  position; this neighbourhood is a documented contributor to hybrid genetic search quality on CVRP
- `pd-pair-relocate` / `pd-pair-swap` — pickup-delivery pairs moved as units with feasible-window
  insertion enumeration

**Acceleration requirements:**
- `don't-look bits` per node; reset only on incident change.
- **O(1) move evaluation via segment concatenation.** Precompute per-route-segment resource
  aggregates (duration, earliest feasible start, latest feasible start, accumulated load, time-warp)
  so any concatenation of segments is evaluable in constant time. This is the single largest
  determinant of local-search throughput and MUST be implemented before any tuning work.
- Under time-dependent travel (§6.3), exact O(1) concatenation is not generally available. Required
  mitigation: evaluate candidate moves against a **fixed-departure lower bound** matrix for
  filtering, then re-evaluate surviving candidates exactly. Record the filter's false-negative rate.

#### ALG-3 — Metaheuristic layer
Two required strategies, both available to the portfolio:

**`ALG-3a` — Hybrid genetic search (HGS).** Population of solutions; fitness-biased parent selection
balancing quality and diversity; problem-specific crossover (selective route exchange, SREX);
intensive local-search "education" of offspring; survivor selection that removes low-quality *or*
highly-similar individuals to preserve diversity; partial restart on stagnation. Feasible and
infeasible sub-populations with adaptive penalty weights are required — allowing controlled
infeasibility is what lets the search cross feasibility barriers.

**`ALG-3b` — Ruin-and-recreate / LNS (SISR).** Iteratively destroy and repair:
- *Ruin*: **adjacent string removal** — remove short contiguous strings of visits that are near one
  another in space, across several routes. This preserves route structure better than random node
  removal and deliberately induces *spatial slack*.
- *Recreate*: **greedy insertion with blinks** — insert removed customers greedily but skip
  ("blink past") the best position with small probability, which cheaply diversifies.
- *Acceptance*: simulated annealing.
- *Fleet minimisation*: a separate procedure using an absence-based acceptance criterion, used when
  vehicle count is the primary objective (`MIN_VEHICLES`).

Additional destroy operators for the adaptive variant (ALNS): random removal, worst removal,
Shaw/related removal, route removal, historical-knowledge removal — with adaptive operator weights
updated from recent success.

**Selection.** The portfolio MUST include at least one HGS member and one R&R member; combining the
two families is itself a documented productive direction.

#### ALG-4 — Penalty management
Adaptive penalties for time-window violation, capacity excess, and duration excess, updated on a
schedule from the observed feasible fraction of recent iterations (target ≈ 25–50% feasible).
Penalties MUST be bounded and reported; unbounded penalty growth is a common cause of search
collapse into trivial solutions.

#### ALG-5 — Route-level exact polishing
After the metaheuristic budget, apply per-route exact or near-exact improvement:
- Optimal sequencing of each route via TSP-with-time-windows dynamic programming where the route
  is short enough (≤ ~14 stops) or via LKH-style variable-depth search otherwise.
- **Optimal departure-time scheduling** per route: minimise duty duration and lateness by shifting
  departure and distributing waiting, respecting driving-hours rules. This is a scheduling problem
  solvable exactly per route and it is nearly free — many production plans leave several percent of
  duty time on the table by departing at the earliest possible moment by default.

#### ALG-6 — Set-partitioning polish (optional, high value)
Collect all distinct routes generated across the whole search into a pool, then solve a
set-partitioning MILP over the pool (each order covered exactly once, vehicle-type counts
respected). With a few thousand columns this solves in seconds and reliably recovers 0.5–2% over
the best single trajectory. Requires that pooled routes be individually verified feasible.

**Measured (T-38).** The 0.5–2% claim is conditional on the pool, and the frozen corpus does not
supply one. Pooling five PyVRP and three OR-Tools runs yields 8–41 columns against ALG-6's "few
thousand", and on `c20-scattered` eight trajectories produce only two distinct order sets — on a
20-customer CVRP that is what optimality looks like. Recovery there is 0.00% on every instance,
because there is no better partition in the pool to find. So T-38's definition of done asserts the
true and useful property on the corpus — the polish never makes an instance worse — and reports the
mean rather than a threshold the corpus cannot express. Where the premise does hold, the claim
reproduces and tracks pool size; on a capacity-pressured 200-customer instance:

| Columns | Recovered | MILP |
|---|---|---|
| 197 | +0.22% | < 1 s |
| 489 | +0.60% | 3 s |
| 977 | +0.89% | 537 s |

"Solves in seconds" holds at the size ALG-6 has in mind and stops holding shortly afterwards, which
is worth knowing before this pass is put on a latency budget.

### 7.6 Large-instance decomposition

Above roughly 2,000–3,000 stops, monolithic search degrades. The orchestrator MUST implement:

**(a) Cluster-first, route-second (initial decomposition).**
Partition orders into sub-problems that are jointly capacity- and time-aware, not merely spatial.
Partition quality dominates final quality, so the partitioner MUST consider spatial proximity,
time-window overlap, and demand balance. Vehicles are partitioned alongside customers so each
subproblem has a coherent sub-fleet. Recent work explicitly shows that fixed partitioning rules
perform inconsistently across instances with differing spatial/demand/operational characteristics,
so the partitioner MUST be adaptive and MUST be evaluated as a component, not assumed.

**(b) POPMUSIC-style iterative sub-problem re-optimisation (improvement).**
Given an incumbent, repeatedly select a seed route, gather its `r` nearest routes, re-optimise that
sub-problem to (near-)optimality with the full-fidelity solver, and re-insert. Iterate until no
sub-problem yields improvement. This is the workhorse for very large real-world instances and
composes cleanly with the portfolio.

**(c) Decompose–route–improve with cross-boundary repair.**
After sub-problems are solved independently, run a **pruned local search across cluster
boundaries**, using the similarity metadata computed during decomposition to prune candidate moves.
Skipping this step leaves visible "seams" at cluster borders, which dispatchers notice immediately.

**Decomposition invariants.**
- `DEC-1` Sub-problem solutions concatenate to a globally feasible solution — depot inventory,
  dock capacity and shared-vehicle constraints MUST be enforced globally, never per cluster.
- `DEC-2` A vehicle appears in at most one sub-problem per round.
- `DEC-3` The global objective is always evaluated by the canonical evaluator, never summed from
  sub-problem objectives (which double-count or omit shared terms).

### 7.7 Concurrency and budget model

- Budget is expressed as `{ wall_ms, max_iterations, target_gap }`; the run stops on the first
  satisfied criterion and records all three.
- Intra-run parallelism: portfolio members on separate cores; decomposition sub-problems in a work
  queue. Shared state is limited to the incumbent pool behind a lock-free exchange.
- Reproducible mode (CON-4) forces single-threaded, iteration-limited execution and is used for all
  regression tests.

### 7.8 Vehicle allocation architecture (Layer D, allocation module)

Allocation spans three horizons. Each is a distinct model consuming the same domain objects.

**Operational (per plan, `FR-30`–`FR-33`).**
Allocation is solved *jointly* with routing by making vehicle deployment endogenous: each vehicle
carries a fixed cost that is charged only if it is used, so the search decides deployment. This is
the Fleet Size and Mix VRP applied to a bounded, heterogeneous, multi-depot fleet, which real
operations require because vehicles differ in equipment, capacity, age and cost, and because
smaller vehicles are the only way to serve access-restricted urban customers.

Required behaviours:
- Empty routes are free and removable at zero cost.
- `MIN_VEHICLES` mode uses the fleet-minimisation procedure (ALG-3b).
- Hired/third-party capacity is modelled as additional vehicles with step-function day costs
  (`OBJ-4`), so the solver reveals the own-vs-hire break-even.
- Report per vehicle: utilisation per capacity dimension, duty utilisation, and **marginal value** —
  the objective delta from re-solving with that vehicle removed (approximated by a short warm-started
  re-solve, exact value not required).

**Tactical (`FR-34`, `FR-35`).**
Two-stage stochastic formulation: first-stage decisions are fleet composition (counts per vehicle
type, per depot); second-stage recourse is routing over sampled demand days, including the cost of
route failure (a vehicle running out of capacity and needing a recovery trip). Solve by scenario
decomposition: enumerate candidate mixes, evaluate each over the scenario set with the operational
solver at reduced budget, and report a cost/service Pareto front. Multi-period stochastic fleet
sizing is a recognised problem class precisely because strategic sizing must account for tactical
planning and operational uncertainty — a deterministic average-day sizing systematically
under-fleets.

**Territory design (`FR-35`).** Produce contiguous, workload-balanced territories from historical
demand density; use as (i) a warm start, (ii) a driver-consistency device (§6.7), and (iii) a
decomposition prior (§7.6a). Territories MUST be re-evaluated on a schedule, with drift reported.

**Depot allocation (`FR-31`).** Where multiple depots can serve an order, depot choice is a decision
variable subject to per-depot inventory. Implement as duplicated candidate start nodes with a
mutually-exclusive group per order, plus a global inventory constraint enforced at Layer E.

### 7.9 Pre-flight infeasibility diagnosis (Layer A)

Before any solving, run cheap per-order feasibility tests and emit the reason codes of §6.5:

1. Skill/access intersection non-empty over eligible vehicles.
2. Order quantity ≤ max eligible vehicle capacity on every dimension.
3. Depot → stop → depot round trip fits inside at least one eligible vehicle's shift, given
   earliest departure and the stop's latest window close.
4. `release_time ≤ latest window close − travel from release location`.
5. Shipment pickup window and delivery window are mutually reachable.
6. Lock set is internally consistent.

Orders failing pre-flight are reported immediately and excluded from the search, which both speeds
solving and — more importantly — gives planners actionable feedback within seconds instead of
after a 15-minute run.

---

## 8. Dynamic and real-time operation (Layer G)

### 8.1 The dispatch-wave model

Same-day and on-demand operations are not static problems solved repeatedly. They are sequential
decision problems under uncertainty. The reference formulation is the **dynamic dispatch waves**
model used in the EURO Meets NeurIPS 2022 competition: the horizon is partitioned into epochs
(e.g. one hour); at each epoch the agent observes the requests known so far and must decide which
to **dispatch now** — committing them to feasible routes — and which to **postpone** so they can be
consolidated with requests that arrive later. Some requests are **must-go**: postponing them makes
their time window unreachable.

**Required components.**

| ID | Component |
|---|---|
| `DYN-1` | **Epoch controller** — advances waves, freezes committed work, publishes plans |
| `DYN-2` | **Must-go classifier** — for each open request, determine whether postponement to the next epoch preserves feasibility under *any* remaining vehicle. Conservative by construction; false negatives are service failures |
| `DYN-3` | **Dispatch policy** — decides the postpone set (§8.2) |
| `DYN-4` | **Committed-state manager** — converts executed and en-route work into `FIX_ROUTE_PREFIX` / `FREEZE_UNTIL` locks (§6.6) |
| `DYN-5` | **Trigger engine** — event-driven re-optimisation on breakdown, cancellation, large ETA drift, new priority order |
| `DYN-6` | **Simulator/replayer** — replays historical days epoch-by-epoch to evaluate policies offline (`AC-3.2`) |

### 8.2 Dispatch policies (implement in this order)

1. **Baselines (mandatory, for calibration).**
   - *Greedy* — dispatch everything known now.
   - *Lazy* — dispatch only must-go requests.
   - *Random* — dispatch must-go plus each other request with probability p.
   These are the competition-standard baselines and MUST be retained permanently as the
   denominator for every policy claim.

2. **Prize-collecting dispatch (`PC-HGS` pattern).** Solve each epoch as a prize-collecting VRPTW in
   which the prize on each non-must-go request encodes how much we want it dispatched now. The
   routing solver then jointly chooses the dispatch set and the routes. Prizes may start as a tuned
   constant and later be **predicted by a learned model** trained so that the resulting solutions
   approach an anticipative oracle. This is the structure that won the competition's dynamic track.

3. **Iterative conditional dispatch (ICD) — sample-scenario approach.** Sample future request
   scenarios, solve each sampled instance, and use consensus across scenarios (requests dispatched
   in most scenarios are dispatched; those dispatched in almost none are postponed) with thresholds
   applied iteratively. Reported to come close to the winning learned approach on the competition
   instances while being far simpler and requiring no training pipeline. **This is the recommended
   default for v1** — it needs no labelled data and degrades gracefully.

4. **Learned policy (optional, later).** Only after ICD is in production and the replayer (`DYN-6`)
   gives a trustworthy offline metric.

### 8.3 Re-optimisation semantics

- Re-optimisation MUST be **stability-aware**: report and optionally penalise churn (stops moved
  between vehicles, ETA shifts communicated to customers). A 0.5% cost gain that reshuffles half the
  plan at 14:00 is a net loss. OR-Tools exposes solution-similarity-to-previous machinery for exactly
  this; where an engine does not, implement churn as a Tier-6 objective term.
- Re-optimisation MUST respect `FREEZE_UNTIL` and never move executed work (`AC-2.2`).
- Every re-optimisation response returns a **delta** (`AC-2.3`), not just a plan.

### 8.4 Latency tiers

| Tier | Trigger | Budget | Method |
|---|---|---|---|
| T0 | Quote / insertion price | ≤ 2 s | Cheapest-insertion over the current plan (VROOM or in-process evaluator) |
| T1 | Single disruption | ≤ 30 s | Locked LNS on affected + neighbouring routes only |
| T2 | Epoch replan | ≤ 5 min | Full dispatch policy + portfolio on open work |
| T3 | Overnight plan | ≤ 60 min | Full portfolio + decomposition + set-partitioning polish |

---

## 9. Data contracts

### 9.1 Principles

- JSON over HTTP for the public API; Protobuf/Arrow internally where volume warrants.
- **All units explicit in field names** (`_m`, `_s`, `_cents`). Unit ambiguity between systems is
  the most common integration defect in routing platforms.
- Unknown fields rejected on input (fail closed), preserved on echo.
- Every request carries an idempotency key; identical key + identical body returns the stored result.

### 9.2 Solve request (abridged, normative shape)

```jsonc
{
  "schema_version": "1.0",
  "problem_id": "plan-2026-08-26-lisbon",
  "horizon": { "start": 1756180800, "end": 1756224000 },
  "objective": {
    "mode": "MIN_COST",
    "tier_overrides": { "balance_weight": 0, "consistency_weight": 0 }
  },
  "rules": { "hours_of_service": "EU-561@2026.1" },
  "options": {
    "seed": 20260826,
    "budget": { "wall_ms": 900000, "max_iterations": null, "target_gap_ppm": null },
    "deterministic": false,
    "portfolio": ["pyvrp:default", "ortools:gls", "lns:sisr"]
  },
  "matrices": {
    "van": { "ref": "mtx:sha256:9f3a...", "time_dependent": true, "bucket_s": 900 }
  },
  "depots": [
    { "id": "D1", "location_id": "L0",
      "dock_capacity": [{ "from": 1756180800, "to": 1756191600, "slots": 8 }],
      "inventory": { "weight_kg": 24000 } }
  ],
  "locations": [
    { "id": "L0", "lat": 38.7223, "lon": -9.1393, "matrix_index": 0 },
    { "id": "L1", "lat": 38.7369, "lon": -9.1427, "matrix_index": 1,
      "access": { "max_weight_kg": 7500, "emission_class_min": "EURO6",
                  "permitted_hours": [{ "from": "07:00", "to": "20:00" }] },
      "dwell_overhead_s": 180 }
  ],
  "orders": [
    { "id": "O-1001", "kind": "JOB", "priority_tier": 0, "prize_cents": 0,
      "release_time": 1756180800,
      "quantities": { "weight_kg": 120, "volume_l": 400, "pallets": 1 },
      "required_skills": ["TAIL_LIFT"],
      "incompatible_with_classes": ["HAZMAT"],
      "delivery": {
        "location_id": "L1",
        "time_windows": [
          { "start": 1756188000, "end": 1756195200, "hardness": "HARD" }
        ],
        "service_fixed_s": 420,
        "service_per_unit_s": { "pallets": 90 }
      } },
    { "id": "O-1002", "kind": "SHIPMENT",
      "pickup":   { "location_id": "L7", "time_windows": [...], "service_fixed_s": 600 },
      "delivery": { "location_id": "L9", "time_windows": [...], "service_fixed_s": 600 },
      "quantities": { "weight_kg": 800 } }
  ],
  "vehicle_types": [
    { "id": "VAN_35T", "routing_profile": "van",
      "capacities": { "weight_kg": 1200, "volume_l": 9000, "pallets": 4 },
      "fixed_cost_cents": 9000, "cost_per_metre_cents": 12,
      "cost_per_second_cents": 5, "overtime_cost_per_second_cents": 12,
      "access_class": "N1", "skills": ["TAIL_LIFT"],
      "max_duration_s": 32400, "reload_allowed": true, "reload_duration_s": 1800 }
  ],
  "vehicles": [
    { "id": "V-01", "type_id": "VAN_35T", "depot_id": "D1",
      "start_location_id": "L0", "end_location_id": "L0",
      "shift": { "start": 1756182600, "end": 1756215000 },
      "driver_id": "DR-77",
      "initial_state": { "drive_used_s": 0, "duty_used_s": 0,
                         "since_last_break_s": 0, "week_drive_used_s": 118800 },
      "available": true }
  ],
  "locks": [
    { "kind": "FIX_ROUTE_PREFIX", "vehicle_id": "V-04", "order_ids": ["O-2001","O-2002"] },
    { "kind": "FORBID_ORDER_ON_VEHICLE", "order_id": "O-1099", "vehicle_id": "V-01" }
  ]
}
```

### 9.3 Solve response (abridged)

```jsonc
{
  "schema_version": "1.0",
  "problem_id": "plan-2026-08-26-lisbon",
  "status": "FEASIBLE",
  "solver": { "winner": "pyvrp:default", "version": "…", "seed": 20260826,
              "iterations": 184203, "wall_ms": 899412,
              "matrix_ref": "mtx:sha256:9f3a…" },
  "objective": {
    "total_cents": 1284300,
    "breakdown": { "unserved_penalty": 0, "fleet_fixed": 810000,
                   "distance": 312400, "duration": 161900,
                   "soft_violation": 0, "balance": 0, "consistency": 0 }
  },
  "routes": [
    { "vehicle_id": "V-01",
      "metrics": { "distance_m": 84210, "duration_s": 27890, "driving_s": 15300,
                   "service_s": 9600, "waiting_s": 1190, "break_s": 1800,
                   "load_peak": { "weight_kg": 1140, "pallets": 4 },
                   "duty_utilisation_ppm": 861000 },
      "steps": [
        { "type": "START", "location_id": "L0", "departure": 1756182600,
          "load_after": { "weight_kg": 1140 } },
        { "type": "DELIVERY", "order_id": "O-1001", "location_id": "L1",
          "arrival": 1756184100, "start_service": 1756188000, "departure": 1756188510,
          "waiting_s": 3900, "load_after": { "weight_kg": 1020 }, "violated": [] },
        { "type": "BREAK", "arrival": 1756199000, "departure": 1756201700,
          "rule_ref": "EC-561/2006 Art.7", "placement": "AT_FACILITY", "location_id": "R12" }
      ] } ],
  "unassigned": [
    { "order_id": "O-1150", "reason_code": "TIME_WINDOW_UNREACHABLE",
      "explanation": "Earliest arrival 14:12 from nearest eligible vehicle V-11; window closes 13:30.",
      "would_fit_if": [{ "change": "window_end", "to": 1756201200 }] } ],
  "allocation": {
    "deployed": 9, "available": 14,
    "by_type": { "VAN_35T": 7, "RIGID_18T": 2 },
    "vehicle_marginal_value_cents": { "V-09": 4200, "V-11": -1500 }
  },
  "verification": { "checked_by": "verifier@1.4.0", "hard_violations": [],
                    "soft_violations": [], "invariants_passed": ["INV-1","…","INV-9"] },
  "warnings": [{ "code": "SNAP_DISTANCE_HIGH", "location_id": "L88", "snap_m": 412 }]
}
```

### 9.4 API surface

```
POST /v1/problems                     -> create + validate, returns problem_id + diagnostics
POST /v1/problems/{id}/solve          -> async job; 202 + job_id
GET  /v1/jobs/{job_id}                -> status, incumbent objective trajectory
GET  /v1/jobs/{job_id}/solution       -> current best (anytime, NFR-03)
POST /v1/jobs/{job_id}/cancel         -> stop, return incumbent
POST /v1/solutions/{id}/reoptimise    -> locks + budget -> delta response
POST /v1/solutions/{id}/quote         -> insertion price for candidate order(s), T0 latency
POST /v1/solutions/{id}/verify        -> run independent verifier on an externally supplied plan
POST /v1/allocation/scenarios         -> tactical fleet sizing sweep (async)
GET  /v1/matrices/{ref}               -> metadata, provenance, coverage
```

`/verify` is deliberately public: it lets integrators check plans produced elsewhere, and it forces
the verifier to be genuinely independent of the solver (CON-1).

---

## 10. Persistence, observability, and operations

### 10.1 Storage

| Store | Contents | Retention |
|---|---|---|
| Object store | Immutable input snapshots, matrices (content-addressed), solutions | Regulatory period |
| Relational | Problems, jobs, solutions index, locks, audit trail | Regulatory period |
| Time-series | Objective trajectories, latency, matrix cache hit rate, violation counts | 13 months |
| Feature store | Service-time and speed calibration features, plan-adherence history | 25 months |

### 10.2 Metrics (minimum set)

**Solver:** time-to-first-feasible, objective trajectory, iterations/sec, incumbent improvement
timestamps, portfolio member win rate, restart count, penalty trajectory, deterministic iteration
count at termination.

**Model quality:** gap to best-known (benchmark corpora), gap to lower bound where available,
unassigned count by reason code, soft violation totals.

**Data quality:** geocode confidence distribution, snap distance p95/p99, matrix cache hit rate,
unreachable-pair count, orders failing pre-flight by reason.

**Operational reality (CON-6):** planned vs actual travel time per arc bucket, planned vs actual
service time per stop archetype, plan adherence (§12.4), on-time delivery rate, overtime hours,
vehicle utilisation, break compliance.

### 10.3 Runbook triggers

| Symptom | Likely cause | First action |
|---|---|---|
| Sudden objective regression across all instances | Matrix version change or OSM extract update | Diff matrix refs; pin previous; re-run frozen corpus |
| Many `TIME_WINDOW_UNREACHABLE` on a normal day | Speed profile or service-time calibration drift | Compare planned vs actual arc times last 7 days |
| Plans feasible but drivers finish 90 min late daily | Service times under-calibrated | Recalibrate from telematics; do not "fix" by padding travel |
| Solver returns `INFEASIBLE` after a lock edit | Conflicting operator locks | Return minimal conflicting lock set to dispatcher UI |
| Latency spike with unchanged instance size | Matrix cache miss storm | Check pre-warm job; check location-master churn |

---

## 11. Verification and validation

### 11.1 Test taxonomy

| Level | Scope | Gate |
|---|---|---|
| L1 Unit | Evaluators, rule engines, matrix adapters | 100% branch on rule engines |
| L2 Property | Randomised instance generators + invariants INV-1..INV-9 | Zero violations over 10⁵ generated cases |
| L3 Golden | Frozen instances + frozen seeds → byte-identical solutions | Exact match (CON-4) |
| L4 Benchmark | Public benchmark sets | §11.3 thresholds |
| L5 Corpus | Frozen anonymised production instances | No regression > 0.5% vs baseline |
| L6 Shadow | Live traffic, plan produced but not executed | Adherence + cost delta reporting |
| L7 Canary | Small depot subset in production | Business KPI parity or better |

### 11.2 The independent verifier (CON-1)

A separate module, in a separate package, with no shared code with any solver, that:

- Recomputes every arrival, departure, load, and cost from the raw route sequences and the pinned
  matrix.
- Evaluates the hours-of-service timeline via the rules engine.
- Checks INV-1 … INV-16 and every lock.
- Is used in CI, at runtime before any plan is published, and via the public `/verify` endpoint.

It MUST be written by a different author than the solver adapter, and MUST NOT import the
evaluator used inside local search. Discrepancies between the two are treated as P1 defects.

### 11.3 Benchmark gates

Public sets to be wired into CI:

| Set | Variant | Purpose |
|---|---|---|
| Solomon (100 customers) | VRPTW | Fast smoke gate, minutes |
| Gehring & Homberger (200–1,000) | VRPTW | Scale + quality gate |
| CVRPLIB / Uchoa (100–1,000) | CVRP | Pure routing quality |
| Li & Lim | PDPTW | Pickup-delivery correctness and quality |
| Cordeau MDVRPTW | MDHVRPTW | Multi-depot assignment and heterogeneous fleet. The set closest to this business's own shape; confirm the instance family and its BKS source when wiring it in |
| EURO Meets NeurIPS 2022 instances | VRPTW static + dynamic | Realistic ORTEC-derived data; the dynamic set is the only public benchmark for the wave model |
| Frozen production corpus | Rich VRP | The only set that reflects your actual constraints |

**Gate policy.**
- Report **gap to best-known solution (BKS)** per instance and the aggregate mean gap, at a
  declared time budget, on declared hardware. A benchmark number without budget and hardware is
  meaningless.
- CI blocks a merge if mean gap on any set worsens by more than 0.25 percentage points versus the
  current baseline, or if any instance regresses by more than 2%.
- Initial targets (to be ratified after the first full baseline run, not before): mean gap to BKS
  ≤ 1% on Solomon at 60 s/instance and ≤ 2% on Gehring & Homberger 1,000-customer instances at
  600 s/instance, single-threaded. **These are targets, not claims** — they MUST be replaced by
  measured baselines in `benchmarks/BASELINE.md` before any external communication.
- The objective convention used for benchmark comparison MUST match the set's published convention
  (for VRPTW-DIMACS-style comparisons: minimise total driving duration, excluding waiting and
  service, with vehicle count unconstrained). Comparing a different objective to published BKS is
  a reporting defect.

### 11.4 Validation against reality

Benchmarks validate the algorithm; only production validates the model.

- **Backtesting.** Replay 90 days of historical orders through the planner; compare planned cost
  and service against what was actually executed.
- **Shadow mode.** Produce plans daily without executing them; measure the gap between the shadow
  plan and the executed plan, and interrogate every large divergence.
- **Canary.** One depot, one month, with explicit rollback criteria agreed in advance.
- **Plan adherence.** See §12.4 — this is the metric that tells you whether the model is right.

---

## 12. Learning and calibration (Layer H)

### 12.1 Service-time calibration
Fit service duration from telematics: `service = f(order_archetype, quantity, location_archetype,
vehicle_type, time_of_day, driver_experience)`. Start with grouped medians per archetype (robust,
explainable) before any regression model. Re-fit monthly; alert on drift.

### 12.2 Speed-profile calibration
Fit per-arc-class, per-bucket speed multipliers from observed GPS traces against the routing
engine's free-flow assumptions. Maintain FIFO by construction (§6.3). Re-fit weekly; hold out one
week for validation.

### 12.3 Prize/dispatch model (optional, §8.2 step 4)
Only after ICD is in production. Train against an anticipative oracle computed offline with full
hindsight over historical days, which is the standard construction for this problem class.

### 12.4 Plan adherence and tacit knowledge (CON-6)

**Measure.** For each executed route, compute a sequence-dissimilarity score between the planned
stop sequence and the actual sequence, plus the realised-cost delta. Aggregate by depot, driver,
territory, and time of day.

**Interpret.** Systematic, repeated deviation is a **model defect**, not driver misbehaviour.
Experienced drivers hold tacit knowledge about roads that are hard to navigate, when traffic is
bad, where parking is findable, and which stops are conveniently served together — information that
is hard or impossible to formalise in an optimisation model, which is exactly why drivers deviate
from planned sequences.

**Act.** In priority order:
1. Extract the deviation into an explicit model feature (a zone, an access rule, a service-time
   archetype, a soft precedence). This is always preferable — it is explainable and auditable.
2. Where the pattern resists formalisation, learn a **sequencing prior** at the zone level and add
   it as a soft objective or a warm-start structure. Zone-sequence learning from historical routes
   is the approach that performed best in the Amazon challenge, where a probabilistic model of zone
   ordering learned from drivers outperformed hand-coded zone constraints.
3. Never simply penalise drivers into compliance with a plan the model got wrong.

**Guardrail.** Learned components MUST be advisory: they may bias search and warm starts, they MUST
NOT be able to produce a plan that violates a hard constraint. The verifier (§11.2) is downstream of
all learning.

---

## 13. Tasks — ordered implementation backlog

Each task lists dependencies, the requirements it satisfies, and a definition of done. Tasks marked
**[GATE]** block the next slice.

**Status — 61 of 65 done**, verified against the repository on 2026-09-02 (912 tests passing, CI
green on `2928a30`). `T-40` was unblocked by separating its construction from its data and turns
out to have been buildable all along; `T-80` and `T-81` were filed in the same edit. Every blocked
row now names the specific thing that would unblock it, so the claim can be checked rather than
inherited. `done` means the task's artefacts exist, are tested and are on `main`; where a
definition of done has a half that needs people or production, the commit says which half is owed
rather than counting the proxy. `blocked` and `optional` are the four that remain:

| ID | Why it is open |
|---|---|
| `T-84` | **Unblocks when** there is a GPU. Unlike the five blockers before it, this one is about the demonstration rather than the data: the definition of done is that a cuOpt run returns a verifiable plan and that §11 can say what it wins, and neither sentence can be written from a machine with no card in it. An adapter compiled from the documentation alone would be untested code wearing an engine's name, which `T-67` refuses by name instead. |

**`T-40`'s row used to read** "`osrm-routed` exposes no departure-time parameter, so there is
nothing to fit FIFO speed profiles against". That conflated two jobs. OSRM cannot *serve*
time-dependent travel, which is true and permanent; but §12.2 fits multipliers *against* the
engine's free-flow assumptions from observed traces, so OSRM's lack of a departure-time parameter
was never what stood in the way. And `T-40`'s own definition of done — a FIFO property test and the
filter's false-negative rate — asks about the construction, not about anybody's traffic. The
construction is `vrp/timedependent.py` and needed no data at all. `T-63` waited on `T-40` and
`T-40` waited on `T-63`'s data, and neither was waiting on the outside world.

None of the remaining four is blocked on effort. A status column goes stale the moment somebody forgets to
update it, so it carries the date and the commit it was checked against — a marker that cannot be
dated is one nobody can trust.

### Slice 0 — Foundations

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-01` | done | Repository scaffold, SDD artefacts, ADR log, CI skeleton | — | CON-* | `constitution.md`, `spec.md`, `plan.md`, `tasks.md` in repo; CI runs lint + tests |
| `T-02` | done | Domain model types (§4) with integer units and exhaustive validation | T-01 | FR-01…FR-16 | Types compile; property tests generate valid/invalid instances |
| `T-03` | done | Canonical evaluator: recompute objective + timeline from a solution | T-02 | INV-9, OBJ-* | Deterministic; unit-tested against hand-computed fixtures |
| `T-04` | done | **[GATE]** Independent verifier package (§11.2) | T-02 | CON-1, INV-1…INV-9 (extended to INV-16 by later tasks) | Separate package, separate author; detects seeded violations in 100% of mutation tests |
| `T-05` | done | Instance generator + property test harness (L2) | T-03, T-04 | §11.1 | 10⁵ random instances produce zero invariant violations |
| `T-06` | done | VRPLIB/Solomon/GH/CVRPLIB/Li&Lim readers + BKS registry | T-02 | §11.3 | All five sets parse; BKS values loaded and versioned |

### Slice 1 — Static core (CVRPTW, single depot, homogeneous)

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-10` | done | OSRM adapter: table + route, snapping, unreachable sentinels | T-02 | MTX-1…MTX-5 | Integration test against a local OSM extract |
| `T-11` | done | Matrix cache, content-addressed versioning, chunking | T-10 | MTX-6, MTX-7, MTX-10 | Cache hit rate metric emitted; 5k-location matrix builds within budget |
| `T-12` | done | PyVRP adapter: model compiler + solution mapper | T-02, T-11 | FR-01…FR-08, CON-3 | Solves Solomon; verifier passes; INV-9 exact |
| `T-13` | done | Objective tiering with instance-derived scaling + staged mode | T-03 | §5.1, FR-13 | Lexicographic dominance proven by property test |
| `T-14` | done | Pre-flight diagnosis + reason codes | T-02 | FR-01, §6.5, AC-1.3 | Every seeded infeasible order gets the correct code |
| `T-15` | done | Solve API (`/problems`, `/solve`, `/jobs`) with idempotency, anytime incumbent | T-12 | NFR-03, §9.4 | p95 latency SLO met on reference instance |
| `T-16` | done | **[GATE]** Benchmark harness + BASELINE.md first run | T-12, T-06 | CON-9, §11.3 | Baseline gaps recorded with hardware + budget; CI regression gate live |
| `T-17` | done | Determinism mode + golden-solution tests (L3) | T-12 | CON-4, AC-1.4 | Byte-identical across 100 repeats and across 2 machines |

### Slice 2 — Rich constraints

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-20` | done | Multi-dimensional capacity incl. peak-load semantics for simultaneous P&D | T-12 | FR-02, FR-03, §6.1 | Peak-load property test; PDPTW (Li & Lim) gate green |
| `T-21` | done | Heterogeneous fleet, per-vehicle costs, multi-depot, open routes | T-12 | FR-07, FR-08 | Verifier-checked on generated multi-depot corpus |
| `T-22` | done | Skills, order↔order incompatibility (incremental class counts), site access | T-12 | FR-10, FR-11, §6.5 | O(1) amortised per move, benchmarked |
| `T-23` | done | Multiple time windows, soft windows with asymmetric penalties, release times | T-12 | FR-04, FR-06 | Fixture set covering all window topologies |
| `T-24` | done | Service-time model (fixed + per-unit + vehicle factor + dwell overhead) | T-12 | FR-05, §6.2 | Verified against telematics fixtures |
| `T-25` | done | **[GATE]** Hours-of-service rules engine: interface + `EU-561` + `US-HOS`, break insertion **inside** route evaluation | T-03 | FR-15, FR-16, §6.4, AC-5.1, AC-5.2 | Compliance fixture suite from regulation text; zero post-hoc break insertion in the codebase |
| `T-26` | done | `initial_state` carry-over from tachograph/ELD input | T-25 | §6.4, AC-5.2 | Partial-duty fixtures plan correctly |
| `T-27` | done | Optional orders / prizes / priority tiers | T-13 | FR-12, FR-13 | Prize-collecting mode reproduces expected drop behaviour |
| `T-28` | done | Multi-trip / reloading with dock queueing | T-21 | FR-09, FR-19, §6.8, §6.9 | Multi-trip corpus; driver-hours interaction verified |
| `T-29` | done | Locks: all kinds + minimal-conflict diagnosis | T-12 | FR-21, §6.6, CON-7 | Conflicting lock sets return minimal IIS |
| `T-30` | done | OR-Tools adapter (expressiveness escape hatch) | T-02 | CON-3, §7.3 | Same domain problem solved by two engines; verifier agrees |

### Slice 3 — Scale and quality

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-33` | done | Granular neighbourhoods + don't-look bits + O(1) segment concatenation | T-12 | ALG-2 | ≥ 10× local-search throughput vs naive; documented profile |
| `T-34` | done | Custom LNS core: SISR ruin (adjacent string removal) + greedy-with-blinks recreate + SA acceptance | T-33 | ALG-3b | Matches published qualitative behaviour on CVRPLIB; portfolio member |
| `T-35` | done | Fleet-minimisation procedure (absence-based acceptance) | T-34 | FR-32, §5.2 | `MIN_VEHICLES` mode reaches BKS vehicle counts on Solomon |
| `T-36` | done | Portfolio runner with shared incumbent pool + canonical scoring | T-30, T-34 | §7.3 | Win-rate telemetry by instance signature |
| `T-37` | done | Decomposition orchestrator: adaptive cluster-first + POPMUSIC sub-problem re-optimisation + cross-boundary pruned local search | T-36 | §7.6, NFR-01 | 10k-stop instance within 60 min; DEC-1…DEC-3 verified |
| `T-38` | done | Set-partitioning polish over the generated route pool | T-36 | ALG-6 | Never worse than the best pooled trajectory on any frozen-corpus instance, with the mean recovery reported; ALG-6's ≥ 0.5% demonstrated separately where its premise holds (see the measurement note under ALG-6) |
| `T-39` | done | Route-level departure-time scheduling + TSPTW polish | T-25 | ALG-5 | Duty-duration reduction measured and reported |
| `T-40` | done | Time-dependent travel: FIFO speed profiles, bucketed evaluation, lower-bound filtering | T-11, T-33 | FR-14, §6.3 | FIFO property test; false-negative rate of the filter reported |
| `T-41` | done | EV range and en-route recharging with charging-time functions | T-33 | FR-20, INV-16 | Range never violated across a generated corpus that contains rounds needing no charge, rounds needing one, and rounds no charge can rescue — the third refused by name rather than planned; a charge is a `CHARGE` step in the timeline, so it pushes every later arrival by its own duration and a time window can notice it; the curve tapers, so the top of a battery costs what it costs. **`COULD` priority**, built because the definition of done asks for a *generated* corpus and generating one needs no charger data |

### Slice 4 — Allocation

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-44` | done | Operational allocation: endogenous deployment, own-vs-hire step costs, marginal value per vehicle | T-21, T-27 | FR-30, FR-33, FR-36 | Allocation block in response; break-even reproduced on fixtures |
| `T-45` | done | Depot allocation with global inventory constraints | T-21 | FR-31, DEC-1 | Stockout produces `DEPOT_STOCKOUT`, never an over-allocated depot |
| `T-46` | done | Scenario engine + tactical fleet sizing sweep API | T-44 | FR-34, US-4 | Cost/service Pareto front over ≥ 30 days × ≥ 10 mixes, unattended |
| `T-47` | done | Territory design + consistency objectives (driver, arrival-time, workload) | T-46 | FR-17, FR-18, FR-35, §6.7 | Consistency cost delta reported against unconstrained optimum |

### Slice 5 — Dynamic operation

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-50` | done | Committed-state manager + `FREEZE_UNTIL` + executed-work locking | T-29 | DYN-4, AC-2.2 | No executed stop ever moves, proven by replay tests |
| `T-51` | done | Epoch controller + must-go classifier | T-50 | FR-22, DYN-1, DYN-2, AC-3.1 | Zero must-go postponements across the replay corpus |
| `T-52` | done | Baseline dispatch policies (greedy / lazy / random) | T-51 | §8.2 step 1 | Permanent baselines wired into the replayer |
| `T-53` | done | **[GATE]** Historical replayer / simulator | T-52 | DYN-6, AC-3.2 | Deterministic replay of 90 historical days; policy comparison report |
| `T-54` | done | ICD sample-scenario dispatch policy | T-53 | §8.2 step 3 | Beats greedy and lazy on the replay corpus; result documented |
| `T-55` | done | Prize-collecting epoch solve (PC pattern) with tuned constant prizes | T-27, T-53 | §8.2 step 2 | Comparable or better than ICD on at least one instance family |
| `T-56` | done | Trigger engine + T1 locked re-optimisation + delta response | T-50 | DYN-5, AC-2.1, AC-2.3, §8.3, NFR-02 (second clause) | p95 ≤ 30 s with 90% locked; churn reported. `NFR-02`'s "locked re-optimisation of one region: p95 ≤ 30 s" is this row's own budget and was measured here before the requirement was traced to it |
| `T-57` | done | Stability/churn objective term | T-56 | §8.3 | Churn/cost trade-off curve produced for operations to choose a point |

### Slice 6 — Learning, explanation, operations

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-60` | done | Explanation service: per-order rationale, marginal costs, `would_fit_if` | T-14, T-44 | CON-5, FR-36 | Dispatcher usability test passed on 20 real queries |
| `T-61` | done | Telematics ingestion + plan-adherence metric | T-15 | CON-6, §12.4 | Adherence dashboard by depot/driver/territory |
| `T-62` | done | Service-time calibration pipeline | T-61 | §12.1 | Monthly re-fit job; drift alerting |
| `T-63` | done | Speed-profile calibration pipeline (FIFO-preserving) | T-40, T-61 | §12.2 | `recalibrate` fits every week but the last and scores itself on the one it did not see; traces generated from a known profile come back at the multipliers that produced them; an arc crossing a bucket boundary is excluded and counted rather than attributed to the hour it left in; FIFO is inherited from `SpeedProfile` and the fit floors the zero multiplier a stalled van implies |
| `T-81` | done | Split `T-40`'s construction from its data, and record what each is blocked on | — | §13 | Every blocked row names the specific thing that would unblock it |
| `T-64` | done | Zone-sequence prior learned from executed routes (advisory only) | T-61 | §12.4 step 2 | Improves adherence with no verifier regressions |
| `T-65` | done | Shadow mode + canary rollout tooling with rollback criteria | T-61 | §11.4 | One depot canary run completed with written go/no-go |
| `T-66` | done | Public `/verify` endpoint | T-04 | §9.4, CON-1 | External plans verifiable; used by at least one integrator |
| `T-67` | done | cuOpt accelerator profile (optional) | T-36 | NFR-09, §7.3 | The profile is a parameter, off by default; with it off the portfolio is the same engines as the same objects and nothing in the solve path asks the import system for `cuopt` at all — checked with a meta-path watcher on a machine that has neither the library nor a GPU, which is the right machine for a negative requirement; with it on and the library absent the run is on CPU and says so rather than falling back silently; the accelerator joins the portfolio and never replaces it. **The adapter itself refuses by name** — see `T-84` |

### Slice 7 — Requirements the catalogue asked for

`FR-23`–`FR-27` came from `CAT-VRP-003` §12.2, which counts the real operations
behind each. `T-79` did not: `NFR-04` has been in this document since version
1.0 and no task ever claimed it, which `tests/test_traceability.py` found rather
than a review. `UC-072` is what makes it concrete -- a matrix provider that
stops responding mid-build -- and that entry is `PARTIALLY_MODELLED` because the
safe half is built and the graceful half is not.

The order below is that count, not preference: the requirement
seven operations ask for is worth more than the one three ask for, and nothing
here is worth starting before the four gaps `T-72` covers, because those are
constraints the engine already claims to enforce and does not.

| ID | Status | Task | Deps | Satisfies | Definition of done |
|---|---|---|---|---|---|
| `T-72` | done | **[GATE]** Compile eligibility into the search: skills, order-class incompatibility, site access, depot inventory | T-12, T-22, T-45 | FR-10, FR-11, FR-31, INV-10, INV-13 | No plan is published that the verifier then rejects on a constraint the model declared |

**`T-72`'s four constraints came out as three mechanisms**, because they are not
four instances of one problem.

Skills (`FR-10`), site access (`FR-11`) and operator locks (`FR-21`) are all
properties of a *(vehicle, order)* pair, which PyVRP expresses with profiles: a
vehicle type routes on its own edge set, and a place it may not enter has no
edge into it. Locks were the surprise -- `FR-21` was checked by `INV-8` and
diagnosed by pre-flight and reached no solve path at all, so a dispatcher's pin
was advice. The encoding restricts places rather than orders, so an instance
where two orders at one address differ in what a vehicle may do is refused
rather than approximated.

Order-class incompatibility (`FR-10`) is a predicate over a route's
*composition* and has no PyVRP encoding; splitting a vehicle into one type per
class would let the search deploy both and plan two vans where the depot has
one. Both adapters refuse it and name the conflict. Refusing is honest and is
not support: a strict xfail holds the goal.

Depot inventory (`FR-31`) is a limit shared across routes, and `DEC-1` puts
those in the orchestrator. `vrp/depots.py` solves, finds the over-draw, withdraws
the marginal work from that depot as `FORBID_ORDER_ON_VEHICLE` locks, and solves
again -- so the search keeps choosing the depot, which is what §7.8 requires and
what `UC-134` warns that a pre-assignment would forfeit. `SolveService` runs it
once before a job finishes; an intermediate incumbent may still over-draw, which
is what `status` already reports about anytime plans.

| `T-73` | done | Multi-period horizon: visit frequency, permitted-day patterns, interval compliance | T-47 | FR-23, §12.2 | A visit-frequency instance plans a horizon, not seven days; compliance measured against the interval and reported per order |

**`T-73`'s coupling is the calendar, and only the calendar.** The seven citing
operations agree on that -- `UC-043`: "The decision is which days to visit which
assets" -- so `vrp/periodic.py` chooses the days and each day is then an
ordinary single-day problem for the ordinary solver. Solving every day jointly
would be a much larger claim and none of the entries makes it.

A recurrence is deliberately not an `Order`. An order is one visit at one place
on one day, which is what the model, the evaluator and the verifier all take it
to be; a commitment to inspect something four times a year is a statement about
orders that do not exist yet. A frequency field on `Order` would have made every
single-day plan carry one meaning nothing.

Compliance counts the gaps at both ends of the horizon, which is `UC-129`'s
point: "the next due date is set by the last visit". Four visits in January is
four visits and eleven months out of compliance, and a measure looking only
between visits calls it perfect.
| `T-74` | done | Maximum ride time from pickup to delivery, for passengers and for perishable goods | T-20 | FR-24, INV-14 | Li & Lim PDPTW still passes; a ride-time-bounded instance never exceeds the bound, and the bound is distinguishable from a delivery window |

**`T-74`'s bound is exact in the verifier and conservative in the search**, and
the difference is worth knowing. `INV-14` measures the journey itself, departure
from the pickup to arrival at the delivery, which is what both `UC-092` and
`UC-157` insist the clock means. `add_shipment` takes no such parameter, so the
adapter derives the one thing it can say soundly: a delivery deadline of the
earliest the collection could physically happen plus the bound. That never
admits a violating plan and can refuse a feasible one, and it is exact when the
collection time is fixed -- a school stop, a booked ward round. Flooring it at
the window's opening instead of at the earliest reachable pickup made ordinary
instances infeasible, because the van still has to drive there.
| `T-75` | done | Separate the sources of priority: commercial tier, SLA clock, statutory obligation | T-13, T-27 | FR-25, FR-13 | Three orders equal on tier and different on source are ordered by source; a statutory obligation cannot be priced; an SLA window is derived at intake |
| `T-76` | done | Route synchronisation: satellite transfer, convoy departure, hub cut-off | T-28, T-37 | FR-26, INV-15, DEC-1 | Two coupled routes meet at a place and time, and the verifier checks the coupling rather than each route alone |

**`T-76` is the first constraint here whose subject is a pair**, and it splits
into two halves that need different answers.

The *timing* half is a loop. `vrp/synchronise.py` solves, sees when the first
echelon actually finished, tells the second it may not start before that, and
solves again. The lever is a time window, which converges for a transfer
because the first half is not constrained by the second and so does not move in
response. A convoy is symmetric and is bounded and reported rather than
promised.

The *different-vehicle* half cannot be done there at all. It is an
order-to-order constraint, the same shape as the class incompatibility `T-72`
refused to compile, and a window says nothing about it: one van collecting
where it just delivered is simply a good route. The first version tried
`FORBID_ORDER_ON_VEHICLE` locks and forbade the second order on every vehicle
in turn until the instance had no answer. It is now refused by name, and the
fix belongs to the instance -- a satellite has a receiving bay and a dispatch
bay, and an HGV cannot get into the second, which `T-72`'s eligibility already
expresses.

`UC-168`'s transhipment is **not** covered. Permitting a shipment to change
vehicle mid-journey breaks `INV-2`'s same-vehicle rule, which FR-26's text does
not ask for and which is a model extension rather than a coupling.
| `T-77` | done | Preemption of planned work by higher-priority arrivals | T-51, T-56, T-75 | FR-27, DYN-5 | Displaced work is re-planned and reported, never silently dropped; churn attributable to preemption is separated from ordinary churn |

**`T-77` turned out to be mostly a reporting task**, which is worth recording
because it looked like a search one. Whose slot an emergency takes is already
decided: `FR-13`'s tiers and `FR-25`'s sources say what may be given up for
what, and `T-75` priced them, so `preempt` pins the executed work and lets the
objective choose. A preemption picking its own victims would be a second,
quieter priority scheme competing with the declared one.

What was missing was the answer. `Delta.moved` reports a reassignment and an
abandonment identically, with `None` standing in for "no vehicle", so a plan
moving three stops between drivers and a plan giving up on three customers were
equally stable by the only measure anyone had. `reassigned` and `displaced`
separate them, and displaced work is reported as `PREEMPTED` naming what took
the slot -- while work that was never planned keeps its own reason, because the
round was already larger than the van and the emergency did not cause that.
| `T-78` | done | Recovery policy for a fleet reduced before the shift starts | T-56 | FR-21, FR-30, FR-32 | A recovery never asks a loaded vehicle to be repacked, and serves what the remaining fleet can carry |
| `T-80` | done | Time-dependent evaluation on the route path: the canonical evaluator, the independent verifier, and §7.5's lower-bound filter | T-40 | FR-14, §6.3, §7.5 | The same sequence of stops finishes later through a peak than at free flow, and the verifier agrees with the evaluator about every arrival |
| `T-82` | done | Planning under a speed profile: a search that carries §7.5's filter rather than refusing the instance | T-80 | FR-14, §7.5, NFR-01 | A route sequenced under a peak profile is one the profile allows, while the same stops sequenced at free flow miss a window when driven through that peak — "beats" is legality, not finish time, and the aware sequence finishes *later* on the clock; §7.5's bound is at or below the exact arc at every departure, so the filtered DP returns what exhaustive search returns; the route polish stays under a second at `MAX_DP_STOPS` |
| `T-91` | done | Process-based parallelism for engines that hold the GIL | T-86 | NFR-05, §7.7 | `run_portfolio(..., executor="process")` runs members on separate interpreters: the repository's own LNS goes from **0.99x across four threads to 3.32x across four processes**, and a test proves the work happened elsewhere by the worker's pid rather than by the answer matching. Winner, scores and report order are unchanged; `workers=1` still runs inline. Two constraints are named rather than discovered: an engine a worker cannot import is refused by `UnsendableEngine` before the pool starts, and the caller needs `if __name__ == "__main__"`. `vrp.snapshot` was **not** needed — `Problem` pickles in 0.09 ms |
| `T-92` | done | Decomposition sub-problems in a work queue | T-37, T-86 | NFR-05, §7.7 | `concatenate(..., workers=N)` and `solve_decomposed(..., workers=N)` run sub-solves through a bounded thread pool, defaulting to one for CON-4's reproducible mode. Concurrency is proven by a barrier a sequential loop cannot pass, and the merge is safe because every cluster owns its own vehicles — pinned by a test, since `assignment.update` would otherwise let completion order decide the plan. **The speed-up was measured and `uc074` was the wrong instance to name**: its sub-solves are ~2 ms, so the pool costs more than it saves and the queue runs at **0.66x**. It pays where §7.7 is aimed — **2.25x** at 300 real deliveries across 8 clusters, **1.92x** at 600 across 10 |
| `T-85` | done | Insertion and removal quotes, priced and timed | T-33, T-56 | NFR-02 (first clause), §7.3, §9.4 | `vrp.quote` prices the cheapest legal placement without replanning: existing routes keep their order and one position opens. **p95 90 ms against the 2 s budget**, 400 stops across 40 vans, twenty runs. The detour picks and the canonical evaluator prices, so the quote is not the scan's own arithmetic; an order no vehicle can take is refused by name rather than quoted at a large number. **The price excludes `unassigned_penalty`** — it dwarfs the running cost, so the raw objective delta reports every insertion as a saving of about a million |
| `T-86` | done | Bounded intra-run parallelism, and isolation somebody has checked | T-36, T-37 | NFR-05, §7.7 | `run_portfolio(..., workers=N)` runs members in a bounded thread pool, defaulting to one because CON-4's reproducible mode is single-threaded and a library that parallelised unasked would make every existing caller irreproducible. Concurrency is *proven* by a barrier two engines cannot both reach unless they are in flight together — a `workers` argument that is accepted and ignored passes every same-answer test and fails that one. The report is collected in portfolio order, so two runs serialise identically. **Speed-up measured, and it is engine-dependent: 3.13x at four workers for PyVRP, 1.00x for the repository's own pure-Python LNS**, on a real round. §7.7's "separate cores" holds only for engines that release the GIL; the rest is `T-91`. Decomposition sub-problems still run serially — see below |
| `T-87` | done | The run record `NFR-06` actually asks for | T-15, T-36 | NFR-06, CON-4 | `vrp.observe.RunRecord` carries all seven, and `NFR_06_FIELDS` names them so a test checks the record against the requirement rather than against a count. The trajectory comes from `lns_search` reporting each incumbent, so a record nothing produces would fail; the hit rate comes from `PairCache` and the violation counts from the independent verifier, and both are tested by moving the thing they measure. `replayable()` separates the iteration index, which is a function of the seed, from the wall clock, which is not. **The row said the record held four of seven; it held three** — `matrix_version` is in it and is not one of `NFR-06`'s seven |
| `T-88` | open | An anonymisation gate for anything leaving the production boundary | T-11 | NFR-07 | The corpora in `vrp/bench` are generated, so no address has ever left; that is a property of where the fixtures came from rather than a control. Done when a corpus derived from real data cannot be written without passing an anonymisation step that strips identities and obfuscates coordinates, and a test proves the unanonymised path refuses by name |
| `T-89` | done | A plan replayable from its own immutable snapshot | T-15, T-53 | NFR-08, CON-4 | `vrp.snapshot` seals a problem and its solver configuration under a sha256 over canonical JSON; `read` refuses a file whose contents no longer hash to its recorded digest; `replay` rebuilds the instance from the record rather than from memory, and the digest is identical across processes with different hash seeds. **The record was not sufficient before this**: the model's own round trip dropped thirty fields — see the note below. Physical retention is §10.1's object store and is not built; what is built is the record that makes retention worth having |
| `T-90` | done | Version the public API before something depends on the unversioned one | T-15 | NFR-10, §9.4 | Every data-plane endpoint is served under `/v1` and, for the window, at the root, from one `data_plane()` router so the two cannot drift; unversioned responses carry `Deprecation` and a `Link` to their successor, and `Sunset` when `API_SUNSET` names a date; the probes and the metrics scrape stay unversioned by design and are not advised to migrate; OpenAPI documents `/v1` as the contract with the root paths derived as `deprecated: true` aliases. Rate limiting folds the two spellings into one bucket and metrics keep them apart, both deliberately. **Rust gateway** |
| `T-84` | blocked | Compile a `Problem` into cuOpt's model, and show the accelerator earns the hardware | T-67 | NFR-09, §7.3, §11 | A cuOpt run returns a plan the independent verifier accepts, and §11's harness reports what it wins and loses against the CPU portfolio by instance signature |
| `T-83` | done | Per-arc-class speed profiles, so a per-arc-class calibration has somewhere to land | T-63 | §6.3, MTX-9, §12.2 | An instance carries `speed_profiles` keyed by arc class rather than one profile for the whole problem, and `travel_between` and §7.5's bound both read the arc's own; a mapping that omits a class the matrix contains is refused by name rather than defaulted to free flow; `SpeedCalibration.as_profiles` applies a whole fit. **Storage is `O(C · T)`, not `MTX-9`'s `O(nnz · T)`** — see the note below |
| `T-79` | done | Graceful matrix degradation: cached fallback, and a `DEGRADED` plan that says so | T-11, T-15 | NFR-04, MTX-11, `UC-072` | A build whose provider fails mid-way returns the cached matrix rather than raising; every plan costed on an incomplete or haversine matrix carries `DEGRADED` out to the caller; `test_uc072_a_degraded_matrix_is_labelled_rather_than_fatal` xpasses |
| `T-93` | done | An attainment measure, so lateness is countable where it is not priced | T-03, T-23 | §6.2, AC-4.2 | `evaluator.window_attainment` reports promises kept along a timeline: `promised`, `on_time`, `lateness_seconds`, `worst_lateness`, and a rate in parts per thousand. It is unpriced and blind to hardness, which is the point -- `soft_penalties` costs SOFT windows only, a HARD window may not carry a rate at all (`TimeWindow.__post_init__` forbids one) and a SOFT one may carry zero, so a stop served two hours after its window shut was accounted at **zero** by every lateness report here, `triggers._lateness` included. Lateness is measured against the last window that closed rather than the first, so a disjoint pair is not overstated by the gap between them, and a stop carrying no window is not counted as promised, since scoring the unconstrained as punctual would let a windowless plan report perfect service. `scenarios.service_level` remains an **assignment** rate by decision, and its docstring now says so rather than claiming "within window" |
| `T-94` | done | Delivery models: an operation described as data | T-89 | FR-02, FR-04, FR-05, NFR-08 | `vrp/servicemodel.py` holds the contract, the schema derived from it, and the builder. `COVERS` and `EXCLUDES` account for **47 of 60** settable fields with a stated reason for each of the 13 exclusions, checked against `dataclasses.fields` rather than a hand-written list -- adding a field to `Vehicle` fails the suite until somebody decides about it, which is the defect `_vehicle_from_dict` shipped at 9 of 28 until `T-89` and the throwaway spike reproduced at 8 of 28. The JSON keys are computed from `COVERS`, so the schema cannot drift from the domain model. `models/signed-envelopes.json` rebuilds `E-94` and `models/mixed-parcels.json` rebuilds `E-21`, locations, orders and vehicles alike; the only differences are the examples' own cosmetic ids, asserted rather than hidden. A fleet that cannot carry its own orders is refused at load with the order named, and an unknown key is refused rather than ignored. `models/categories.json` maps categories to models globally and is checked in both directions the way `config.rs` checks `app.env`. **Not delivered:** fragments, deployment overlays and the tunable surface are the composition rules, and the run configuration is `T-95`. Plan: `docs/planning/DELIVERY_MODELS_PLAN.md` |
| `T-95` | open | The run configuration a model does not own | T-94, T-13 | §5.1, §5.3, NFR-08 | `ObjectiveSpec` (mode, tiers, rates), solver iterations, seed and engine are not in `Problem`, so a model that fixes the fleet has not pinned the plan: two runs of one model can order two plans differently and both be right. Declared as a sibling section, resolved separately, sealed into the snapshot beside the model hash. Anything dynamic -- `epochs`, `preemption`, `committed`, replan triggers -- is explicitly out of scope: those operate on a sequence of problems, and one model builds one |
| `T-96` | open | The gate that makes model-driven configuration safe | T-94, T-95, T-46, T-93, T-65 | FR-34, §11.4, AC-4.2 | Moving configuration from code to JSON moves a class of failure from review time to run time, and this buys it back. `vrp/compare.py` runs candidate models over identical demand, reusing `scenarios.sweep` and `pareto`, and reports cost from **verifier-accepted plans only**, `window_attainment` rather than `MixResult.service_level` (an assignment rate that would score a model serving everything late as perfect), and **which constraint bound** -- the number that says which parameter to adjust, which nothing reports today for a plan that succeeded. Structural validation of composite models is separate and solve-free: disjoint sections, no chains, overlay inside the tunable surface, every field traceable to one source. A variant failing a gate its master passes is attributed by reverting each overlay field in turn, which is bounded only because the overlay is one level deep. `rollout.py` ships the canary half already |

**`UC-171`'s claim was right and the measurement was wrong**, which is worth
recording because the backlog said the opposite. Two things were being compared
unfairly. §8.4's cheapest-insertion recovery is built for a mid-day disruption
with most of the day committed, and applied at shift start it had the whole
round to place at once and dropped half of it. And the alternative it lost to --
a free re-plan of the reduced fleet -- was being scored on a freedom the depot
does not have, because at 05:30 the remaining vans are already packed.

Loading is a commitment, and `FR-21` is how the entry says to express it:
`committed.loading_locks` pins what is aboard a van that is coming, and
`triggers.recover_from_absence` forbids the vans that are not and solves. On the
fixture that recovery serves 12 of 12 rather than 6, moves 6 stops rather than
12, leaves the objective unchanged rather than ten times worse, and asks nothing
already loaded to move. The last of those is the guarantee; the rest is what
stops it being a worse plan.

---

## 14. Risks and mitigations

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| `R-1` | Garbage input data silently produces plausible-looking bad plans | Severe, chronic | Pre-flight diagnosis (T-14), snap-distance and geocode-confidence gates, data-quality dashboard (CON-2) |
| `R-2` | Objective drift between local-search evaluator and ground truth | Severe, silent | INV-9 enforced in CI and at runtime; independent verifier (T-04) |
| `R-3` | Breaks inserted post hoc, making published plans infeasible | Severe, legal | T-25 gate: break scheduling inside route evaluation, forbidden as a post-pass |
| `R-4` | Matrix scaling wall at n² | High | Sparsification (MTX-8), chunking (MTX-7), pre-warmed cache (MTX-10) |
| `R-5` | Over-fitting to distance while drivers optimise for time and access | High | Plan-adherence loop (§12.4); calibrated service times before any solver tuning |
| `R-6` | Weighted-sum objective inverts at scale | High | Lexicographic tiering with instance-derived scaling and a staged fallback (§5.1) |
| `R-7` | Solver lock-in | Medium | Adapter architecture (CON-3); at least two engines maintained (T-30) |
| `R-8` | Dynamic policy tuned on a biased replay corpus | Medium | Permanent baselines (T-52); corpus refresh policy; hold-out days |
| `R-9` | Churn destroys dispatcher trust despite cost gains | Medium | Churn objective term (T-57) and delta-first responses (AC-2.3) |
| `R-10` | Benchmark numbers quoted without budget/hardware context | Medium | Gate policy in §11.3; `BASELINE.md` is the only citable source |
| `R-11` | Learned components producing infeasible or unexplainable plans | Medium | Learning is advisory only; verifier is downstream of all learning (§12.4 guardrail) |
| `R-12` | Time-dependent travel breaks O(1) concatenation, collapsing throughput | Medium | Lower-bound filtering with measured false-negative rate (T-40) |

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **ALNS** | Adaptive Large Neighborhood Search — destroy/repair with adaptively weighted operators |
| **BKS** | Best-Known Solution for a benchmark instance |
| **ConVRP** | Consistent VRP — bounds driver and arrival-time variation across a horizon |
| **CVRP / VRPTW / PDPTW** | Capacitated VRP / with Time Windows / Pickup-and-Delivery with Time Windows |
| **MDHVRPTW** | Multi-Depot Heterogeneous VRPTW — several depots, mixed vehicle types, customer time windows. The shape this business actually has (§3.4) |
| **TSP / TSPTW** | Travelling Salesman Problem, optionally with time windows — sequencing one vehicle's stops. The degenerate case of every VRP variant, and what OSRM's `/trip` solves |
| **Duty** | A driver's continuous work period, bounded by working-time law |
| **FIFO property** | No-passing: departing later on an arc can never mean arriving earlier |
| **FSM-VRP** | Fleet Size and Mix VRP — vehicle composition is a decision variable |
| **HGS** | Hybrid Genetic Search — population + intensive local search + diversity management |
| **HOS** | Hours of Service — driving-hours regulation |
| **IIS** | Irreducible Infeasible Subsystem — minimal conflicting constraint set |
| **Must-go** | A request whose postponement would make its time window unreachable |
| **POPMUSIC** | Partial Optimization Metaheuristic Under Special Intensification Conditions — iterative sub-problem re-optimisation of a large incumbent |
| **R&R / SISR** | Ruin-and-Recreate / Slack Induction by String Removals |
| **Time warp** | A modelling device permitting a controlled time-window violation during search, penalised in the objective |
| **Wave / epoch** | A discrete decision point in dynamic dispatch |

---

## 16. References

Method and algorithm foundations:

1. Vidal, T. (2022). *Hybrid genetic search for the CVRP: open-source implementation and SWAP\* neighborhood*. Computers & Operations Research 140:105643.
2. Wouda, N. A., Lan, L., & Kool, W. (2024). *PyVRP: A High-Performance VRP Solver Package*. INFORMS Journal on Computing 36(4):943–955. https://doi.org/10.1287/ijoc.2023.0055 — https://pyvrp.org
3. Christiaens, J., & Vanden Berghe, G. (2020). *Slack Induction by String Removals for Vehicle Routing Problems*. Transportation Science 54(2):417–433.
4. Ropke, S., & Pisinger, D. (2006). *An Adaptive Large Neighborhood Search Heuristic for the Pickup and Delivery Problem with Time Windows*. Transportation Science 40(4):455–472.
5. Shaw, P. (1998). *Using constraint programming and local search methods to solve vehicle routing problems*. CP 1998.
6. Taillard, É., & Voss, S. (2002). *POPMUSIC — Partial Optimization Metaheuristic Under Special Intensification Conditions*.
7. Toth, P., & Vigo, D. (2014). *Vehicle Routing: Problems, Methods, and Applications*. SIAM.
8. Golden, B., Assad, A., Levy, L., & Gheysens, F. (1984). *The fleet size and mix vehicle routing problem*. Computers & Operations Research 11:49–66.
9. Ichoua, S., Gendreau, M., & Potvin, J.-Y. (2003). *Vehicle dispatching with time-dependent travel times*. European Journal of Operational Research 144:379–396.
10. Goel, A. (2010) and Goel, A. (2012). *The minimum duration truck driver scheduling problem*. EURO Journal on Transportation and Logistics.
11. Groër, C., Golden, B., & Wasil, E. (2009). *The Consistent Vehicle Routing Problem*. M&SOM.
12. Kovacs, A. A., Parragh, S. N., & Hartl, R. F. (2015). *The Generalized Consistent Vehicle Routing Problem*. Transportation Science.

Competitions, data sets, and real-world evidence:

13. Kool, W., et al. (2023). *The EURO Meets NeurIPS 2022 Vehicle Routing Competition*. PMLR v220. — https://euro-neurips-vrp-2022.challenges.ortec.com/
14. ORTEC. *euro-neurips-vrp-2022-quickstart* (baseline dispatch strategies: greedy / lazy / random). — https://github.com/ortec/euro-neurips-vrp-2022-quickstart
15. van Doorn, J., et al. *An iterative sample scenario approach for the dynamic dispatch waves problem* (ICD). arXiv:2308.14476.
16. Merchán, D., et al. (2022). *2021 Amazon Last Mile Routing Research Challenge: Data Set*. Transportation Science. — https://routingchallenge.mit.edu/about-the-challenge/
17. Wu, C., Song, Y., March, V., & Duthie, E. (2022). *Learning from Drivers to Tackle the Amazon Last Mile Routing Research Challenge*. arXiv:2205.04001.
18. DIMACS 12th Implementation Challenge — Vehicle Routing.
19. Solomon (1987) VRPTW instances; Gehring & Homberger extended instances; CVRPLIB/Uchoa; Li & Lim PDPTW instances.

Regulation:

20. Regulation (EC) No 561/2006 — driving times, breaks and rest periods.
21. Directive 2002/15/EC — organisation of working time for mobile road transport workers.
22. FMCSA Hours of Service regulations, 49 CFR Part 395.

Engines and infrastructure:

23. Perron, L., & Furnon, V. *Google OR-Tools* routing solver. — https://developers.google.com/optimization/routing/routing_options
24. Kurtz, J., et al. *OR-Tools' Vehicle Routing Solver: a Generic Constraint-Programming Solver with Heuristic Search for Routing Problems*. — https://research.google/pubs/or-tools-vehicle-routing-solver-a-generic-constraint-programming-solver-with-heuristic-search-for-routing-problems/
25. Coupey, J. *VROOM — Vehicle Routing Open-source Optimization Machine*. — https://github.com/VROOM-Project/vroom
26. Project OSRM. — https://project-osrm.org · Valhalla. — https://github.com/valhalla/valhalla
27. NVIDIA cuOpt. — https://github.com/nvidia/cuopt · https://docs.nvidia.com/cuopt/

Methodology:

28. GitHub Spec Kit — Spec-Driven Development. — https://github.com/github/spec-kit · https://github.github.com/spec-kit/

---

## 17. Amendment procedure

1. Any change to §1 (Constitution) requires sign-off from engineering, operations, and compliance.
2. Changes to §2–§6 (Specification) require a new `FR-*`/`NFR-*` identifier; existing identifiers
   are never reused or renumbered.
3. Changes to §7 (Plan) require an ADR recording the alternatives considered and the benchmark
   evidence, per CON-9 and CON-10.
4. Every amendment increments the document version and records the affected task IDs so the
   backlog stays traceable.

### Amendment log

| Version | Change | Affected tasks |
|---|---|---|
| 1.1 | Added §3.4, mapping the named problem classes — TSP, CVRP, VRPTW, MDHVRPTW, PDPTW — onto the requirements that compose them, the benchmark set that exercises each, and the slice that delivers it. **MDHVRPTW was previously unnamed anywhere in this document** despite being the shape §2.1 describes, and TSP appeared only as a polish technique in §7.5 rather than as a class the platform serves. Added the corresponding glossary entries and a Cordeau MDVRPTW row to §11.3. No requirement was added, renumbered or reused: §3.4 is a mapping over the existing `FR-*` set, per rule 2. | `T-12`, `T-13`, `T-20`, `T-21`, `T-23`, `T-39` |
| 1.2 | Closed five traceability gaps found by auditing the document against itself. `FR-19` (dock synchronisation) and `FR-22` (partial dispatch) were each implied by a task's own title but claimed by neither, so nothing traced them: added to `T-28` and `T-51`. `FR-20` (EV range) appeared in no task at all and was not excluded either — a requirement with no owner — and now has `T-41`, marked `COULD` and flagged as the only task with no data source in the current stack. §6.2 cited `T-42`, which does not exist; service-time calibration is `T-62`. `ALG-3`'s two strategies were referenced as `ALG-3a`/`ALG-3b` but labelled only **(a)**/**(b)**, so the identifiers dangled; they are labelled now. | `T-28`, `T-41`, `T-51`, `T-62` |
| 3.2 | Pinned the two `PARTIALLY_MODELLED` catalogue entries that `tests/test_traceability.py` had been reporting for as long as the audit has existed: `UC-045` (clinic siting, an LRP) and `UC-175` (postal walks, a CARP). Each now has a passing test for the half the system supports — `FR-34`'s scenario sweep, `FR-35`'s territory design — and a **strict** xfail for the half it does not: nothing in `vrp/` chooses which sites to open, and `StopSpec` has no vocabulary for demand on a street segment. Strict is the point: an entry whose gap lives only in prose is one nobody notices closing, so the xfail xpasses and *fails the suite* if somebody implements it, which forces the status line to be updated rather than quietly becoming wrong. No requirement or status changed — this records a boundary that was already there. | — |
| 3.1 | Closed `T-91`, and the doubt recorded against it was wrong. I argued twice that a process pool might not pay because a `Problem` must be pickled per sub-solve; measuring first, a pickle round trip is **0.09 ms** for a 24-order instance and spawn amortises across the work. Four members of a pure-Python engine: 2.651 s serial, 2.664 s across four threads (**0.99x** — the GIL), 0.831 s across four processes (**3.19x**). So the deliverable was the pool after all, and `vrp.snapshot` was not needed for it. Processes buy two constraints and both are named rather than left to be discovered: an engine a worker cannot import — a lambda, a closure, a local function — is refused by name **before** the pool starts, because the standard library's `BrokenProcessPool` says nothing about which member was at fault; and a spawned worker re-imports the caller's main module, which no library can fix. The test that a silent fallback to threads cannot pass is the one that reads the worker's pid: same winner, same scores and same report order are all things a thread pool would also give. | `T-91` |
| 3.0 | Closed `T-85`, `NFR-02`'s first clause. Two defects surfaced from writing the tests against the canonical evaluator rather than against the quote's own arithmetic. **The price was dominated by `unassigned_penalty`**: on a twelve-stop instance one unserved order is worth a million against forty-five thousand of distance, so the raw objective delta said inserting a stop *saved* 998,800. The penalty is how a solver is told to prefer serving an order; it is not a price, and quoting it would tell a customer their delivery pays for itself — so a quote is the change in the cost of *serving*. **And the placement scan compared whole-route lengths across vehicles**, which always picks the emptiest van however long the detour onto it; it compares detours now. Both were found by asserting the quote against `evaluate` over both plans, which is the same reason `CON-1` keeps a verifier away from a solver. The property that makes it a quote rather than a replan — every other route untouched — has its own test, because a function that returned a better plan with everything moved would pass every price and latency check and be useless to the dispatcher it is for. | `T-85` |
| 2.9 | Closed `T-92`, §7.7's second kind of intra-run parallelism. The definition of done said to measure the speed-up **on `uc074`**, and measuring it there returned **0.66x** — the queue is slower than the loop on that fixture, because each sub-solve takes about two milliseconds and the thread pool costs more than it saves. The fixture is named for sitting at the decomposition *threshold*, which is a statement about order count and not about how much work a cluster is; specifying it was my error and the row now carries the figure rather than the instruction. On instances the requirement is actually about the queue pays: 2.38x at 300 orders across eight clusters, 1.95x at 600 across ten. That is the argument for `workers=1` remaining the default rather than a concession. Two smaller corrections came out of perturbing it: the merge's safety rests on `_assign_vehicles` giving each cluster its own vehicles rather than on collection order — the docstring claimed the latter — and nothing tested that each sub-solve carries `seed + cluster.index`, so collapsing every cluster onto one seed would have left the plan identical and every test passing. Both now have tests. | `T-92` |
| 2.8 | Closed `T-86`. The definition of done said "parallel and serial portfolios return the same winner", which is the trap in this requirement: it is satisfied perfectly by a `workers` argument that is accepted and ignored, and by a pool that happens to run everything in sequence. The test that cannot be passed that way is a **barrier** two engines can only both reach if they are genuinely in flight together, and the mirror of it — a pool of one must time out, because CON-4's reproducible mode is single-threaded rather than nearly so. Collection order turned out not to decide the winner (a `min` over a name-keyed dict with a name tiebreak is order-independent by construction) but to decide the **report**, and two runs agreeing about every number while listing them differently serialise to different bytes — so the docstring's original claim was corrected rather than kept. **The speed-up was measured and it is not uniform**: 3.00x at four workers for PyVRP, which is C++ and releases the GIL, and 1.00x for the repository's own pure-Python LNS, which does not. Reporting only the first figure would have somebody size a box on a speed-up half the portfolio cannot have. Filed `T-91` for the process pool that would fix it and `T-92` for §7.7's other half, the decomposition work queue, which this task did not touch. | `T-86`, `T-91`, `T-92` |
| 2.7 | Closed `T-87`. The row claimed `Solution.solver` covered four of `NFR-06`'s seven; it covered **three** — seed, solver version and the iteration count `CON-4` demands. `matrix_version` is in the record and is not one of the seven, which is the sort of arithmetic that makes a gap look smaller than it is. The four that were missing existed in pieces nothing collected: `PairCache` counted its own hits, the verifier returned violations, and the objective trajectory was recorded nowhere. The design risk in this requirement is a **decorative** record — every field is easy to emit and hard to make true, and a trajectory that is always empty passes any test that asks whether the field exists — so each field is tested by running the same thing twice with one dimension changed and asserting it moved. `NFR-06` asks for timestamps and `CON-4` asks for replayability, which are two clocks: both are kept, and `replayable()` names the half that is a function of the seed rather than leaving somebody to work out which fields to ignore. The search reports incumbents through an optional recorder and is otherwise unchanged — a test pins that the same seed returns the same plan watched or not, because observability that moves the answer is worse than none. | `T-87` |
| 2.6 | Closed `T-89`, and the snapshot was the smaller half of it. `Problem.from_dict` — the model's own round trip, and the thing a replay would have to stand on — reconstructed **nine of `Vehicle`'s twenty-eight fields, four of `Order`'s thirteen, three of `StopSpec`'s five, and none of the problem's locks, synchronisations or speed profiles**. Thirty fields, dropped in silence: every cost, the hours-of-service rule set, site access, skills, batteries, reloads, order incompatibilities, ride times, and `NFR-04`'s own `degraded` marker. A problem that went through it came back cheaper to serve and legal in more ways, and a snapshot built on it would have replayed that and reported a faithful match — the exact audit failure `NFR-08` exists to prevent. Nothing caught it because the single test round-tripping a problem used one with none of those fields set. The codec is now total, and `vrp.bench.fixtures.maximal_problem` plus a guard in `tests/vrp/test_snapshot.py` keep it that way: the guard fails when a dataclass gains a field the fixture leaves at its default, which is the mechanical version of the discipline that was missing. **Retention is not built** — §10.1's object store is a deployment decision, and the row says so rather than implying a filing cabinet exists. | `T-89` |
| 2.5 | Closed `T-90`. The gateway served §9.4's surface unversioned, so there was no way to make a breaking change and keep a promise. Two hazards were found by writing the tests before the routing, and neither would have shown up as a failure: `Limits::for_path` matched exact strings, so `/v1/route` would have served the same handler **with no rate limit at all**, and `handler_label` would have dropped every versioned request into the `none` bucket. Both now strip the prefix through one function, with opposite policies stated where they are made — the limiter folds the spellings into one bucket, because separate ones hand a client double its quota for the cost of alternating; the metrics keep them apart, because the window is over when unversioned traffic reaches zero and nothing else can say that. A third came from the tests after: `/health` was being told to migrate to a `/v1/health` that does not exist. Probes and the scrape are unversioned *by design* rather than awaiting migration, so the advisory now fires only where a successor exists, and a test pins the advised set against the rate-limited one. `API_SUNSET` is empty by default: the deprecation is always announced, and only an operator can commit to when the window closes. | `T-90` |
| 2.4 | Filed `T-85`–`T-90` for the six requirements `tests/test_traceability.py` had been reporting as claimed by no task: `NFR-02`, `NFR-05`, `NFR-06`, `NFR-07`, `NFR-08` and `NFR-10`. The audit has listed them since it was written and nothing acted on the list, which is how `NFR-04` sat unowned until `T-79`. Two findings from reading them against the code rather than filing six placeholders. **`NFR-02` was half met and untraced**: its "locked re-optimisation: p95 ≤ 30 s" clause is `T-56`'s own budget, measured in `tests/vrp/test_reoptimisation_latency.py`, and `T-56` cited `DYN-5` and `§8.3` but never the requirement — the same shape as `NFR-04`, so `T-56` now cites it and `T-85` covers only the quote clause nothing measures. **`NFR-10` is simply not met**: the gateway's routes carry no version, so there is no way to make a breaking change and keep a promise. None of the six is filed blocked; each definition of done is written to be checkable on this hardware, which is the correction the previous six blockers earned. | `T-56`, `T-85`, `T-86`, `T-87`, `T-88`, `T-89`, `T-90` |
| 2.3 | Closed `T-67`, whose blocker was the sixth of the pattern and was still being defended one message before it was checked. It read "needs GPU hardware not present in this environment"; the definition of done asks for a feature flag and an unaffected CPU path when disabled. `NFR-09` is a **negative** requirement — the core must run on commodity CPU and a GPU must never be a hard dependency — so the machine best placed to demonstrate it is one with no GPU. Off now means untouched rather than tried-and-failed: a meta-path watcher asserts the solve path never asks the import system for `cuopt`, and the watcher carries a sentinel import so it cannot pass by being detached. What genuinely needs the hardware is compiling a `Problem` into cuOpt's model and showing the accelerator earns the machine, filed as `T-84`; `cuopt_engine` refuses by name until then, because an adapter written from the documentation alone would be untested code wearing an engine's name. | `T-67`, `T-84` |
| 2.2 | Closed `T-83`, and **its blocker was false when written**. It claimed every arc class the calibration had seen was `local`, so one profile per instance had not yet cost an answer. That was measured against a corpus invented for `T-63`'s example; measuring the twenty-seven `vrp.bench` fixtures instead, sixteen span two or three classes, most of those are majority `arterial` or `trunk`, and the eleven that span one are the degenerate fixtures with two to six arcs. One profile therefore says congestion slows a motorway exactly as it slows a side street, which is the arithmetic behind §6.3's own failure mode. The fifth blocker in this sequence to argue value and call it feasibility, after `T-40`, `T-80`, `T-63` and `T-41` — and the first written *after* `T-81` audited the pattern. **What was built is `O(C · T)`, not `MTX-9`'s `O(nnz · T)`**: the class is derived from the arc's free-flow duration in constant time rather than stored, and per-*arc* profiles are not fittable anyway — an individual arc is driven too rarely for §12.2 to estimate one. `MTX-9` still describes storage nothing builds; that is now stated rather than implied by a task claiming to have built it. | `T-83` |
| 2.1 | Closed `T-41` and added `INV-16`. The blocker said there was no data source for charger locations or charging curves; the definition of done asks for a **generated** EV corpus, and a generated corpus needs neither — the fourth time a row argued value and called it feasibility, after `T-40`, `T-80` and `T-63`. Range is a state a *detour* replenishes on a non-linear curve rather than a capacity dimension, so PyVRP refuses an electric fleet by name and `vrp.electric` places the stops around a solved route, the shape `DEC-1` gives and `vrp.depots` already uses. Charging is a `CHARGE` step rather than a total added afterwards, which is what the definition of done's second half is for: an hour on a plug that delays the rest of the day can break a time window, and an hour in a report cannot. Where to charge is greedy and says so — choosing charge points jointly with the route is the electric VRP proper and is a search problem, not a repair one. | `T-41` |
| 2.0 | Closed `T-63`. The blocker said the missing thing was telematics volume; the definition of done asks for a weekly re-fit and a held-out validation report, and both are properties of the pipeline rather than of the traffic. Traces generated by driving a known profile are what makes the fit checkable at all — against real telematics nobody knows the answer, so nobody can tell a working pipeline from one that returns free flow every week, which is the argument for the generated corpus rather than a concession about it. **The fit found a gap in the model**: §12.2 fits per arc class and §6.3 specifies per-arc profiles, while `Problem.speed_profile` carries one for the whole instance. The classes are kept apart and the caller names one, per `CON-11` — collapsing them would describe no road in particular. Filed as `T-83`, blocked on a measured need rather than on effort. | `T-63`, `T-83` |
| 1.9 | Closed `T-82` on the route polish rather than the metaheuristic. `vrp/lns.py` and `vrp/localsearch.py` are distance-based and carry no clock, so §7.5's filter has nothing to attach to there; `tsptw_sequence`'s Held-Karp state already tracks `ready`, which is exactly the departure time the Ichoua–Gendreau–Potvin construction needs, and the dominance argument survives time-dependence only because §6.3's no-passing property holds. **The row's dependency on `T-63` was the third instance of the mistake `T-81` audited**: a search that carries a profile is testable against any profile, and only its *usefulness* waits on fitted traffic. The definition of done said a built plan "beats" an evaluated one, which measurement contradicted — the congestion-aware sequence finishes later on the clock and is the only one that is legal — so the row now says legality. `NFR-01` is a measured 2.2x constant on a step already bounded at `MAX_DP_STOPS`. | `T-82` |
| 1.8 | Closed `T-80` for the evaluation path and filed `T-82` for the planning one. `T-80`'s blocker was written a day earlier and repeated the mistake it was written to correct: it said wiring the construction in "before `T-63`" would be arithmetic about congestion nobody measured, which is an argument about value rather than feasibility, and the task's definition of done asks only that a peak be slower than free flow. Integrating it surfaced that PyVRP compiles one duration per arc, so an instance carrying a profile is now refused by name rather than returning a plan the verifier rejects at every arrival. | `T-80`, `T-82` |
| 1.7 | Unblocked `T-40` by separating the construction from the data, and filed `T-80` for the integration and `T-81` for the audit of the blocker notes themselves. `T-40`'s row claimed OSRM's lack of a departure-time parameter left "nothing to fit FIFO speed profiles against"; §12.2 fits multipliers *against* free flow from observed traces, so that was never the obstacle, and the task's own definition of done asks about the construction rather than about real traffic. `T-40` and `T-63` had been waiting on each other. Every blocked row now states what would unblock it, which is the only form of the claim anybody can check. | `T-40`, `T-63`, `T-80`, `T-81` |
| 1.6 | Filed `T-79` for `NFR-04` (graceful matrix degradation), defined since version 1.0 with no task claiming it — found by `tests/test_traceability.py`, not by review. The requirement asks for two things and the system has one: a mid-build provider failure propagates rather than fabricating arcs, so nothing silently substitutes haversine, but there is no cached fallback and no `DEGRADED` label on a plan costed against an incomplete matrix. `UC-072` is the operation and is `PARTIALLY_MODELLED` for exactly that split. No requirement was added or changed. | `T-79` |
| 1.5 | Recorded `INV-10`–`INV-15` in §4.3, which the independent verifier has enforced without the specification naming them. §4.3 listed nine invariants and §11.2 claimed the verifier "checks INV-1 … INV-9"; it checks fifteen. Each was added when a requirement arrived that none of the first nine covered — compatibility and site access, reloads, dock capacity, depot inventory, ride time, and route synchronisation — and each covers a plan that satisfied every invariant §4.3 named while being one nobody could drive. Found by a mechanical audit of the repository rather than by review, which is the point: a specification that understates what the system checks invites somebody to rely on a guarantee it does not know it makes. No requirement was added or renumbered; this records behaviour that already exists. | `T-04`, `T-22`, `T-28`, `T-45`, `T-74`, `T-76` |
| 1.4 | Drafted `CON-11`, which states how a constraint reaches the plan: the model carries it, the verifier checks it exactly, the search is told whatever can be said soundly, and what cannot be said is refused by name rather than assumed. **Proposed, not in force** — §17.1 requires sign-off from engineering, operations and compliance before a §1 change binds, and that has not been recorded. Written from Slice 7, where the same choice was made independently seven times and where `T-72` and `T-76` between them found five constraints that were checked by the verifier and compiled into the search nowhere. No requirement, task or identifier was changed. | `T-72`, `T-73`, `T-74`, `T-75`, `T-76`, `T-77`, `T-78` |
| 1.3 | Corrected the slice labels in §3.4's `Delivered by` column. Two rows cited a task from the wrong slice: VRPTW listed `T-23` under Slice 1 and PDPTW listed `T-13` under Slice 2, where §13 places them in Slice 2 and Slice 1 respectively. Both classes genuinely span two slices, so each task now carries its own slice rather than one label covering both. No task, requirement or composition changed — only the labels naming where each already sits. | `T-13`, `T-23` |

*End of document.*
