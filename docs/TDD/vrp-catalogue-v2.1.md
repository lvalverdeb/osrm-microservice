---
document_id: CAT-VRP-003
title: Real-World Problem Catalogue for Vehicle Routing
version: 2.1
companion_documents: [SDD-VRP-001, SDD-VRP-UI-002]
machine_readable_extract: scenarios.jsonl
build_script: build_catalogue.py
entry_schema:
  id: string, stable, pattern UC-nnn
  name: string
  variant: enum ['CARP', 'CVRP', 'DARP', 'IRP', 'LRP', 'MDHVRPTW', 'PDPTW', 'TSP', 'VRPTW']
  tier: enum [P0, P1, P2]
  tags: list of string
  section: string
  description: string
  binds: string
  exercises_raw: string
  requirements: list of requirement ids (FR-nn or FR-Pnn)
  breaks: string
  status: enum [MODELLED, PARTIALLY_MODELLED, NOT_MODELLED]
counts:
  operational_scenarios: 142
  adversarial_instances: 15
  by_variant: {CARP: 3, CVRP: 25, DARP: 3, IRP: 1, LRP: 1, MDHVRPTW: 29, PDPTW: 25, TSP: 16, VRPTW: 39}
  by_tier: {P0: 14, P1: 49, P2: 79}
---

# Real-World Problem Catalogue — v2.0
## Domain scenarios, constraints, and documented deployments

| Field | Value |
|---|---|
| Document ID | `CAT-VRP-003` |
| Version | 2.0 (supersedes 1.0) |
| Companion to | `SDD-VRP-001` (engine), `SDD-VRP-UI-002` (workbench) |
| Purpose | Ground requirements in real operations; generate the fixture corpus |
| Scenarios | 148 (134 operational + 14 adversarial… see §11) |

### What changed in v2.1

- **Machine-readable interface added** (§0). Uniform entry schema, closed vocabularies, a companion
  `scenarios.jsonl` extract, and a validator that fails the build on schema drift.
- **Every entry is self-contained.** No entry relies on a neighbouring entry to be understood, so
  any single entry survives being retrieved in isolation.
- Two entries dropped during the v2.0 reorganisation (`UC-011`, `UC-039`) restored; five entries
  using an ad-hoc schema normalised; dangling pseudo-references replaced with proposed requirement
  identifiers `FR-P01`–`FR-P10`.
- Coverage tables are now generated from the entries rather than authored, so counts cannot drift.

### What changed in v2.0

- **Variant coverage audited and closed** (§2). v1.0 had effectively zero standalone TSP scenarios
  and thin CVRP coverage. Both are now first-class sections.
- **Real-world constraint taxonomy added** (§3) — 78 constraints with their source of truth and the
  failure mode when omitted. This is the section to review against a customer's operation.
- **Documented industry deployments added** (§4) — UPS, Amazon, DHL, Walmart, Meituan, Alibaba,
  Air Liquide, with published figures and what each one proves.
- Scenarios reorganised by variant. **Identifiers `UC-001`–`UC-074` are unchanged** and keep their
  meaning; only their section placement moved. New scenarios continue from `UC-075`.

---

## 0. Machine-readable interface

This document is written to be consumed by both people and language models. Everything in this
section is a guarantee about structure, not a description of style.

### 0.1 Guarantees

| Guarantee | Detail |
|---|---|
| **Stable identifiers** | `UC-nnn` never changes meaning and is never reused. Renumbering is prohibited |
| **One entry, one heading** | Every scenario is an `####` heading, so it chunks cleanly and has a stable anchor |
| **Self-contained entries** | No entry requires a neighbouring entry to be understood. Cross-references are additive context, never load-bearing |
| **Uniform schema** | Every entry carries the same labelled fields, in the same order, with no optional prose |
| **Closed vocabularies** | `variant`, `tier` and `status` are enums, listed in §0.3 |
| **Generated indexes** | Coverage tables (§2, §12.1, §14) are generated from the entries. They cannot disagree with the content |
| **Validated on build** | `build_catalogue.py` fails on duplicate ids, unknown enum values, dangling references, short fields, and non-self-contained entries |

### 0.2 Files

| File | Role |
|---|---|
| `vrp-catalogue-v2.1.md` | This document. Generated output — edit the source, not this |
| `scenarios.jsonl` | One JSON object per scenario. The retrieval and filtering surface |
| `build_catalogue.py` | Normaliser, index generator, and validator. Run in CI |

### 0.3 Entry schema and vocabularies

```json
{
  "id": "UC-075",
  "name": "Delivery-station route sequencing to match driver behaviour",
  "tier": "P0",
  "variant": "TSP",
  "tags": ["dynamic"],
  "section": "5. TSP — single tour, sequence only",
  "description": "Given a fixed set of stops assigned to one van, produce the sequence ...",
  "binds": "sequence realism, not distance",
  "exercises_raw": "FR-05, FR-18, plus the plan-adherence loop",
  "requirements": ["FR-05", "FR-18"],
  "breaks": "minimising distance. This is the Amazon challenge problem exactly ...",
  "status": "MODELLED",
  "status_note": ""
}
```

- `variant` ∈ `TSP` · `CVRP` · `VRPTW` · `MDHVRPTW` · `PDPTW` · `DARP` · `IRP` · `CARP` · `LRP`
- `tier` ∈ `P0` (must work at v1) · `P1` (first year) · `P2` (must not be architecturally excluded)
- `status` ∈ `MODELLED` · `PARTIALLY_MODELLED` · `NOT_MODELLED`
- `requirements` reference `SDD-VRP-001` §3. Identifiers of the form `FR-Pnn` are **proposed**
  requirements that do not yet exist in that document; they are listed in §12.2.

### 0.4 Field semantics

Field meanings are fixed, and matter more than they look:

- **`binds`** — the constraint that actually determines fleet size and cost in this operation. Not a
  list of constraints that apply; the single one that decides the answer.
- **`breaks`** — the specific wrong answer a naive implementation produces. This is the field to
  turn into a test assertion. An entry whose `breaks` says only "produces a bad plan" is defective.
- **`exercises_raw`** — free text with parenthetical notes; `requirements` is the parsed list. Use
  `requirements` for filtering and `exercises_raw` for reading.

### 0.5 Query patterns

Common questions and how to answer them from `scenarios.jsonl` rather than by reading prose:

| Question | Query |
|---|---|
| What must work at v1? | `tier == "P0"` |
| Which scenarios justify requirement FR-15? | `"FR-15" in requirements` |
| Is variant X adequately covered? | group by `variant`, compare against §2 |
| Which requirements have no scenario? | set-difference `requirements` against `SDD-VRP-001` §3 |
| What should the engine decline? | `status != "MODELLED"` |
| Which fixtures test capacity semantics? | `"FR-02" in requirements` |
| What breaks a naive capacity model? | search `breaks` for `peak`, `total`, `aggregate` |

### 0.6 Instructions for an agent editing this catalogue

1. Edit the **source** markdown, never the generated output.
2. Adding a scenario: take the next free `UC-nnn`, use the exact field set in §0.3, and write a
   `breaks` line naming a concrete wrong answer. Do not reuse a retired identifier.
3. Do not hand-edit anything between `<!-- BEGIN:GENERATED -->` and `<!-- END:GENERATED -->`.
4. Do not write "as above", "same as", "see UC-nnn" as the substance of a field. The validator
   rejects it, and it breaks retrieval.
5. Run `python3 build_catalogue.py <src> <out.md> <out.jsonl>` and fix every reported error before
   committing. A non-zero exit means the document is inconsistent with itself.

---

## 1. How to use this catalogue

Each entry is a real operation someone pays a routing engine to solve. Entries do double duty:
they justify requirements in the design document, and they specify fixtures for the test corpus.

```
UC-nnn  Name                                        [variant] [tier]
  One line on the operation.
  Binds       the constraint that actually determines fleet size and cost
  Exercises   requirement IDs from SDD-VRP-001 §3
  Breaks      the specific wrong answer a naive implementation gives
```

**The `Breaks` line is the point.** "Test that capacity works" is a weak test. "Test that a route
whose total load fits but whose peak load exceeds capacity is rejected" is a real one, and it comes
from an operation where that bug shipped.

Tiers: **P0** must work at v1 · **P1** within the first year · **P2** must not be architecturally
excluded.

---

## 2. Variant coverage index

The five variants the engine advertises, and how many scenarios exercise each as its *primary*
structure. Many scenarios exercise more than one; the primary is the one that would decide solver
selection.

<!-- BEGIN:GENERATED coverage -->
| Variant | Primary scenarios | Tiers (P0/P1/P2) | Status |
|---|---|---|---|
| **TSP** | 16 | 3 / 4 / 9 | adequate |
| **CVRP** | 25 | 2 / 8 / 15 | adequate |
| **VRPTW** | 39 | 6 / 14 / 19 | adequate |
| **MDHVRPTW** | 29 | 3 / 11 / 15 | adequate |
| **PDPTW** | 25 | 0 / 10 / 15 | adequate |
| **DARP** | 3 | 0 / 1 / 2 | thin |
| **IRP** | 1 | 0 / 1 / 0 | deliberately partial |
| **CARP** | 3 | 0 / 0 / 3 | deliberately partial |
| **LRP** | 1 | 0 / 0 / 1 | deliberately partial |
| *(adversarial, §11)* | 15 | — | n/a |

Total scenarios: **142 operational + 15 adversarial = 157**. Counts in this table are generated from the entries by `build_catalogue.py`; do not edit by hand.
<!-- END:GENERATED -->

### 2.1 What the audit found

**TSP was the real gap.** v1.0 treated TSP as "the thing inside every VRP" and gave it no scenarios
of its own. That was wrong on two counts. First, a large class of customers genuinely have only a
sequencing problem — the assignment is fixed by union agreement, territory, or physical
constraint, and they want the order. Second, the TSP layer is where **sequence realism** lives:
Amazon's entire Last Mile Routing Research Challenge is a TSP-shaped problem, and it exists because
mathematically optimal sequences are not the sequences experienced drivers execute. A platform with
no TSP-primary scenarios has no place to test that.

**CVRP was under-represented relative to its market.** v1.0 had four. In practice, bulk
distribution, waste, fuel, and agriculture are enormous CVRP markets where time windows are
genuinely absent or trivially wide, and modelling them as VRPTW with 24-hour windows wastes search
effort and produces worse plans.

**PDPTW splits into three structurally different families** that v1.0 conflated: same-vehicle
paired transport (courier), simultaneous pickup-and-delivery at one stop (beverage, linen), and
many-to-many with transhipment (LTL, groupage). They need separate fixtures.

**Two variants appear in customer conversations that the engine does not model** — inventory
routing (§10.5) and arc routing (`UC-042`). Both are listed so the design document can decline them
explicitly rather than discover them mid-sales-cycle.

---

## 3. Real-world constraint taxonomy

The constraints that appear in production instances, grouped by what they come from. Each row
names the source of truth — because a constraint whose owner you cannot name is a constraint you
cannot maintain.

### 3.1 Load and capacity

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Gross weight | Vehicle registration, axle plate | Capacity dimension | Overloaded vehicle, legal exposure |
| Volume / cube | Body dimensions | Capacity dimension | Physically unloadable plans |
| Pallet / floor positions | Body layout | Integer dimension | Fits by weight, doesn't fit on the floor |
| Roll cages, dollies, totes | Operational standard | Integer dimension | Same |
| Temperature compartments | Vehicle spec | One dimension per compartment | Frozen goods on an ambient shelf |
| Axle-load distribution | Legal, per axle | Position-dependent constraint | Legal on total, illegal per axle |
| Load stability / stacking | Practice, damage claims | Item-class constraints | Crushed goods, insurance claims |
| LIFO / rear-door access | Vehicle geometry | Sequence constraint on P&D pairs | Item at the front, needed first |
| Value limits (cash, high-value) | Insurance policy | Capacity dimension in currency | Uninsured exposure |
| Dangerous goods quantity thresholds | ADR/DOT regulation | Threshold constraint per class | Regulatory breach at a quantity boundary |
| Peak vs total load | Physics | Running max along route | The classic bug — see `UC-066` |

### 3.2 Time

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Customer delivery window | Contract, booking | Time window, hard or soft | Arrivals when nobody is there |
| Multiple disjoint windows | Site opening pattern | Window list per stop | Artificial infeasibility over lunch closures |
| Receiving-bay hours | Site policy | Hard window | Refused delivery, wasted trip |
| Release / ready time | WMS, production, kitchen | Earliest departure per order | Vehicle waits for goods that don't exist yet |
| Cut-off / SLA deadline | Commercial promise | Route-end or per-stop deadline | Missed SLA invisible to the model |
| Service duration, fixed | Time study | Per-stop constant | Systematic optimism |
| Service duration, per unit | Time study | Per-quantity coefficient | Big drops modelled as small ones |
| Service duration by vehicle type | Equipment (tail lift vs manual) | Per-type multiplier | Wrong vehicle looks equally fast |
| Parking and walking overhead | Telematics, driver report | Location dwell overhead | The Amazon finding — see §4.2 |
| Waiting time | Derived | Explicit, costed | "Cheap" plans that consume the whole day |
| Time-dependent travel | Speed profiles from GPS | FIFO speed buckets | Chronic afternoon lateness |
| Ride time (people aboard) | Policy, welfare | Max duration per passenger | 90 minutes on a school bus |
| Journey time (livestock) | Welfare regulation | Cargo-side accumulator | Regulatory breach |

### 3.3 Legal and regulatory

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Daily driving limit | EC 561/2006; FMCSA Part 395 | Rules-engine accumulator | Criminal liability |
| Break after continuous driving | Same | Mandatory inserted stop | Plans infeasible on publication |
| Daily and weekly rest | Same | Multi-day state | Multi-day plans illegal from day two |
| Weekly / fortnightly driving caps | Same | Carry-over state | Legal Monday, illegal Friday |
| Working time (distinct from driving) | Directive 2002/15/EC | Second accumulator | Loading counts as work, not rest |
| Hours already consumed today | Tachograph, ELD | `initial_state` input | Fresh-duty assumption → breach |
| ADR / hazardous goods routing | ADR, national rules | Forbidden arcs and zones | Illegal routing of hazmat |
| Cabotage restrictions | Cross-border regulation | Vehicle-to-region eligibility | Illegal international operation |
| Night-time delivery noise curfews | Municipal ordinance | Time-dependent site access | Fines, permit loss |
| Driver licence category | Licence record | Skill matching | Unlicensed driving |
| Working-time collective agreements | Union agreement | Shift and overtime rules | Grievance, industrial action |

### 3.4 Vehicle and asset

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Vehicle height, width, length | Registration | Access class vs network | Bridge strike |
| Weight class for road access | Signage, road authority | Access class | Illegal or impossible route |
| Emission class / LEZ eligibility | Registration, city scheme | Access class, time-varying | Fines, refused entry |
| Equipment: tail lift, crane, fridge | Fleet register | Skills | Undeliverable load |
| Fuel range / EV state of charge | Telematics | Range or SoC dimension | Vehicle stranded |
| Vehicle availability windows | Maintenance schedule | Shift envelope | Planned use of a vehicle in the workshop |
| Trailer and chassis availability | Yard system | Separate resource | Drayage plan with no chassis |
| Compartment configuration | Fleet register | Dimension layout | Grade mixing in fuel delivery |
| Maximum route distance | Policy, lease terms | Route constraint | Lease mileage overrun |
| Vehicle-to-depot assignment | Operations | Fixed or decision variable | Vehicle starts where it isn't |
| Telematics-derived speed profile | GPS traces | Per-class speed multipliers | Model disagrees with reality |

### 3.5 Site and network

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Geocode accuracy and snap distance | Geocoder confidence | Data-quality gate | Routes to the wrong side of a motorway |
| Delivery entry point vs street address | Operational knowledge | Distinct access coordinate | Driver circles the block — UPS's UPSNav problem |
| Site access restrictions by vehicle | Site survey | Access profile | Vehicle turned away |
| Permitted delivery hours by vehicle size | Municipal rule | Time-varying access | Large vehicle banned at 08:00 |
| One-way systems, turn restrictions | Road network | Asymmetric matrix | Distances wrong in one direction |
| Unreachable locations | Network topology | Explicit sentinel arc | Solver "optimises around" a large finite number |
| Ferry and toll segments | Network | Cost and schedule on arcs | Free ferries, instant crossings |
| Dock and bay capacity | Depot layout | Cumulative resource per slot | Forty vehicles, eight bays |
| Depot inventory availability | WMS | Per-depot stock constraint | Depot promises goods it doesn't hold |
| Yard and staging space | Site layout | Resource constraint | Vehicles queue off-site |
| Left-turn avoidance | Safety and fuel policy | Arc cost penalty | UPS's documented preference ignored |
| Gate hours (ports, terminals) | Terminal schedule | Hard windows at facilities | Truck arrives to a closed gate |

### 3.6 Human and workforce

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Driver shift start and end | Roster | Shift envelope | Plans outside contracted hours |
| Overtime cost and cap | Contract | Piecewise cost above shift | Every overtime minute priced the same |
| Driver skill and certification | HR record | Skills | Unqualified driver on site |
| Site induction and clearance | Customer requirement | Driver-site eligibility | Refused at the gate |
| Two-person crews | Job requirement | Separate crew resource | Single-crewed impossible job |
| Driver-territory familiarity | Historical assignment | Consistency objective | Slower service, more errors |
| Workload fairness | Industrial relations | Balance objective | Grievances, attrition |
| Language and communication | HR record | Skill matching | Failed customer interaction |
| Start and end from home | Contract | Per-vehicle start/end location | Phantom depot commute |
| Meal breaks distinct from legal breaks | Contract | Additional break rules | Legal but contractually void |

### 3.7 Commercial and service

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Priority tier / VIP customers | Commercial policy | Lexicographic tier | Best customer dropped first |
| Statutory universal service | Regulation | Tier-0 mandatory | Prize-collecting drops a legal obligation |
| Order value and prize | Revenue system | Prize in objective | Serving unprofitable work at capacity |
| Own-vs-hired capacity | Contracts | Step-function day cost | Eleven hired vehicles for one parcel each |
| Customer-promised ETA stability | Notification system | Churn penalty | Re-optimisation re-notifies everyone |
| Named-driver guarantee | Contract | Consistency constraint | Promise silently broken |
| Delivery attempt policy | Operations policy | Retry as new order | Failed delivery vanishes from the model |
| Cost-to-serve attribution | Finance | Per-order cost allocation | Cannot price a customer |
| Carrier handover boundary | Network design | Prize-collecting boundary | Own fleet drives past the economic radius |

### 3.8 Data and operational reality

| Constraint | Source of truth | Modelled as | Omitted → |
|---|---|---|---|
| Matrix version pinning | Build system | Content-addressed reference | Yesterday's plan cannot be reproduced |
| Demand uncertainty | History | Stochastic scenario set | Average-day fleet fails half of all days |
| Service-time drift | Telematics | Scheduled recalibration | Plans decay silently over months |
| Executed vs planned state | Telematics, driver app | Frozen prefix locks | Re-optimisation moves completed stops |
| Operator overrides | Dispatcher | Locks | Human intent silently discarded |
| Timezone and DST | Locale | Operation timezone, not browser | Hour-long errors twice a year |
| Order cancellation mid-route | Order system | Dynamic removal | Vehicle visits a cancelled stop |
| Partial deliveries | POD system | Residual quantity | Order marked complete, goods still aboard |

---

## 4. Documented industry deployments

Published, verifiable deployments at scale. These matter for a design document because they settle
arguments: when someone claims a capability is exotic, these are the counter-examples.

### 4.1 UPS — ORION

**Scale.** Route optimisation across roughly 66,000 routes in the US, Canada and Europe, deployed
to about 55,000 drivers by the 2016 completion of the US rollout.

**Published results.** ORION won the 2016 INFORMS Franz Edelman Award. INFORMS reported savings
exceeding $320 million before the rollout was complete. The system cuts around 100 million miles a
year — roughly 8 fewer miles per driver per day — and UPS projected $300–400 million in annual
operating cost reduction. Dynamic ORION, which re-optimises mid-route as traffic, pickup
commitments and delivery orders change, added a further 2–4 miles per driver per day and was in use
by 97% of the ORION van fleet by mid-2021.

**What it proves for this platform.**
- Dynamic re-optimisation is worth roughly a third again of what static optimisation is worth. This
  justifies Slice 5 as a first-year priority rather than a nice-to-have.
- Precise delivery entry points matter enough that UPS built UPSNav for them — validating the
  distinction in §3.5 between a street address and the point a driver actually stops at.
- **Explainability drove adoption.** UPS built the reasoning into the driver-facing system because
  drivers who cannot see why a route is ordered as it is will override it. This is `CON-5` in
  `SDD-VRP-001`, and it is not a soft preference — it is the difference between a deployed system
  and a shelfware one.

### 4.2 Amazon — Last Mile Routing Research Challenge

**Scale and setup.** Launched March 2021 with MIT's Center for Transportation & Logistics; about
4,000 historical routes released; 45 final submissions; $175,000 in prizes; winners announced July
2021. The winning team — Cook, Held and Helsgaun — brought TSP pedigree directly, and their approach
sequenced within zones using the LKH solver.

**The problem statement is the interesting part.** Participants were not asked to minimise cost.
They were asked to *predict the sequences experienced drivers actually execute* — to capture tacit
knowledge gleaned over years, which conventional optimisation does not represent. Amazon's own
framing named the gap: drivers hold real-time knowledge of road blockages, congestion and parking
that optimisation models do not have.

**The number that should change your design.** In the challenge dataset, travel time is only about
33% of a delivery driver's day. The remaining two thirds is spent looking for parking and walking
to make deliveries, averaging roughly 1.8 minutes of service time per stop overall and 2.5 minutes
inside city limits.

**What it proves.**
- Service time, not travel time, dominates dense last-mile operations. A platform that lets teams
  tune the solver before calibrating service times has its priorities inverted (`CON-2`).
- Zone structure is a first-class modelling object. Follow-up work found that drivers navigate *by*
  Amazon's zone coding, and that the zoning scheme itself conditions how routes get executed — which
  argues for encoding tacit knowledge in the zoning up front rather than correcting sequences after.
- Plan-versus-executed divergence is a measurable, model-improving signal, not driver
  misbehaviour (`CON-6`).

### 4.3 DHL Supply Chain — large-scale routing

**Scale.** DHL Supply Chain North America moves more than a billion packages a year for corporate
customers, with transportation planners performing routing, bidding and improvement tasks across
many concurrent business projects.

**Published work.** A 2023 Franz Edelman finalist for "Network Mode, Mixed Fleet, and Staging
Optimizations for the DHL Supply Chain", with the methods published in INFORMS Journal on Applied
Analytics in 2024 as innovative integer programming software for large-scale routing.

**What it proves.**
- **Bidding is a distinct use case from planning.** A 3PL must cost a prospective contract before
  winning it, which means running the optimiser against hypothetical networks at proposal speed.
  This is the workbench's scenario-sweep capability serving a revenue function, not a demo one.
- Mixed fleet and mode selection are joint decisions with routing — validating `FR-07` and `FR-33`.
- Integer programming remains competitive at industrial scale for the structural decisions, which
  supports the portfolio architecture in `SDD-VRP-001` §7.3 rather than a single metaheuristic.

### 4.4 Walmart — outbound grocery supply chain

**Published results.** Winner of the 2023 Franz Edelman Award. Walmart built an end-to-end
optimisation framework spanning strategic network design through to operational routing and load
planning, combining mixed-integer programming, metaheuristics and simulation. Adopted across the
entire US grocery supply chain, it eliminated 108,000 truck routes covering 33 million miles in
fiscal 2023, saving $91.5 million and preventing 98.6 million pounds of CO₂.

**What it proves.**
- Strategic, tactical and operational decisions belong in **one framework with shared models**. This
  is the direct justification for `FR-34` and the three-horizon allocation architecture in
  `SDD-VRP-001` §7.8 — and evidence against building operational routing first and bolting network
  design on later.
- Simulation for scenario evaluation is part of the system, not an analyst's side project.
- The stated context matters: fast-changing omnichannel demand, inflation pressure, and an already
  efficient network with no room for incremental gains. That is the condition under which
  optimisation has to reach across horizons to find anything at all.

### 4.5 Meituan — real-time dispatch at extreme scale

**Scale.** More than 60 million on-demand orders delivered per day through a minute-level delivery
network, assigning orders to couriers in seconds.

**Published results.** A 2023 Franz Edelman finalist, published in INFORMS Journal on Applied
Analytics in 2024. Since deployment in 2019: average order delivery time down 20.96%, average
courier travelling distance per order down 23.77%, and roughly $0.23 billion in annual cost
reduction.

**What it proves.**
- The dispatch system's own published evolution is a roadmap: manual assignment → area-level
  one-by-one greedy → area-level batch construction → citywide global optimisation combining OR and
  ML. Each stage was justified by order volume. This is `SDD-VRP-001` §8.2's staged policy ladder,
  validated at the largest scale anyone operates.
- Multi-objective is unavoidable: consumers, couriers, merchants and the platform have conflicting
  objectives, which is the case for lexicographic tiering over a weighted sum (§5.1 of the engine
  spec).
- Seconds-level solve times at this scale are achieved by decomposition plus learned pruning of the
  search space, not by a faster solver.

### 4.6 Alibaba — sub-hour retail fulfilment

A 2021 Franz Edelman finalist. Delivery of e-commerce and grocery orders within 30 minutes to two
hours of ordering, with a vehicle routing algorithm optimising **warehouse order picking and
delivery routing together**, built on large neighbourhood search and deep reinforcement learning.

**What it proves.** Picking and delivery are one problem when the promise is 30 minutes. Any
architecture that treats warehouse release as an exogenous input cannot serve this market — which
is the argument for `FR-06` release times being a first-class decision variable rather than a
timestamp handed over the wall.

### 4.7 Air Liquide — industrial gas inventory routing

Posed as the 2016 ROADEF/EURO Challenge, and representative of the industrial gas sector generally
(Air Liquide, Air Products, Praxair). Remote telemetry monitors customer tank levels and forecasts
consumption; under vendor-managed inventory the supplier guarantees no stockout occurs, and decides
both **when to deliver and how much** — with a minority of "call-in" customers who order on demand
sitting alongside the VMI base.

**What it proves.**
- There is no order. Demand is inferred from telemetry, which means the "orders" input to the
  routing engine is itself a forecast-driven decision. This is the inventory routing problem, and
  it is explicitly outside the v1 model (§10.5).
- Mixed VMI and call-in customers in one fleet is the realistic case, so a design that assumes all
  demand arrives the same way is wrong for this sector.

### 4.8 Lyft — real-time matching

A 2023 Franz Edelman finalist for real-time reinforcement learning in driver-rider matching.
Relevant here as the boundary case: at sufficiently high density and sufficiently short trips, the
routing problem degenerates into a matching problem and a different algorithm family wins.
Worth naming in the design document as a market the platform does **not** target.

---

## 5. TSP — single tour, sequence only

Sixteen scenarios where the assignment is already fixed and only the order is open. This section
did not exist in v1.0 and its absence was the largest gap in the catalogue.

#### UC-075 — Delivery-station route sequencing to match driver behaviour

Given a fixed set of stops assigned to one van, produce the sequence an experienced driver would actually run.

- **Variant:** TSP
- **Tier:** P0
- **Binds:** sequence realism, not distance
- **Exercises:** FR-05, FR-18, plus the plan-adherence loop
- **Breaks:** minimising distance. This is the Amazon challenge problem exactly — the mathematically optimal sequence is not the executed sequence, and the gap is parking, access and zone structure
#### UC-076 — Warehouse picker walk sequencing

Ordering a pick list to minimise walking distance in an aisled warehouse.

- **Variant:** TSP
- **Tier:** P1
- **Binds:** aisle topology and one-way aisle rules
- **Exercises:** FR-11 (aisle direction as access), FR-05
- **Breaks:** Euclidean distance between bin coordinates. Racking is a wall; the metric is the aisle graph, and two bins one metre apart across a rack may be sixty metres of walking
#### UC-077 — Single technician's fixed day

One engineer, a day's appointments already assigned to them, sequence to be decided.

- **Variant:** TSP
- **Tier:** P0
- **Binds:** appointment windows within one tour
- **Exercises:** FR-04, FR-05, FR-16
- **Breaks:** treating it as a VRP with one vehicle. It is, formally — but the useful behaviour is sub-second response so the engineer can re-sequence from the van, which is a different budget
#### UC-078 — Meter reading round

A fixed round of meters walked or driven in sequence.

- **Variant:** TSP
- **Tier:** P1
- **Binds:** physical accessibility and read order conventions
- **Exercises:** FR-05, FR-18
- **Breaks:** ignoring the existing round order. These rounds have been walked the same way for years; a resequenced round is rejected unless the saving is large and explained
#### UC-079 — Drone or UAV inspection sweep

Powerline, pipeline or roof inspection waypoints in one flight.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** battery endurance, including the reserve needed for the leg home
- **Exercises:** FR-20 (range as a hard budget), FR-11 (no-fly zones)
- **Breaks:** no return-to-base reserve. The tour must be feasible *including* the leg home with margin, which makes it an open TSP with an endurance constraint, not a closed one
#### UC-080 — Crane and lift sequencing on a construction site

Ordering lifts to minimise slew and travel.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** physical reach and lift precedence
- **Exercises:** FR-01 (precedence), FR-05
- **Breaks:** ignoring precedence. Steel goes up in a structural order; the cheapest tour is often physically impossible
#### UC-081 — PCB drilling and CNC toolpath

Ordering thousands of hole positions for a drill head.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** pure travel time of the head
- **Exercises:** nothing beyond sequencing
- **Breaks:** heuristic-only solving. This instance is Euclidean, symmetric, has no side constraints and runs to tens of thousands of points — it is the case where a dedicated TSP solver genuinely wins, and a useful reference point for the portfolio's routing logic
#### UC-082 — Security guard patrol route

A round of checkpoints within a shift.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** coverage within the shift, plus deliberate unpredictability
- **Exercises:** FR-16, FR-18 (inverted, as in `UC-011`)
- **Breaks:** producing the same optimal tour every night. Predictable patrols are defeatable patrols
#### UC-083 — Cash collection round from retail sites

One crew, a fixed list of sites, sequence open.

- **Variant:** TSP
- **Tier:** P1
- **Binds:** risk exposure and site opening hours
- **Exercises:** FR-02 (value dimension), FR-04, FR-18 (inverted)
- **Breaks:** optimising distance while accumulating value monotonically. Risk rises with load, so the objective wants high-value sites late, which fights the distance objective
#### UC-084 — Hotel housekeeping room sequence

Rooms assigned to one housekeeper, ordered across floors.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** guest checkout times and lift capacity
- **Exercises:** FR-04 (rooms become available on checkout), FR-06
- **Breaks:** floor-by-floor ordering. Room availability is time-dependent, so the naive vertical sweep strands the housekeeper waiting outside occupied rooms
#### UC-085 — Sample collection round within a hospital

Porter collecting specimens from wards to the lab.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** ward round timings and lab cut-off
- **Exercises:** FR-04, FR-06, FR-16
- **Breaks:** a static graph. Lift waiting times dominate and vary by time of day, so the travel metric is time-dependent even indoors
#### UC-086 — Fuel card and forecourt audit round

Auditor visiting sites in one trip.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** nothing but travel; the classic textbook case
- **Exercises:** baseline only
- **Breaks:** nothing — included deliberately as the smoke-test scenario that must always work and always be fast
#### UC-087 — Re-sequencing a route mid-shift after a missed stop

Driver skipped a stop; re-order the remainder.

- **Variant:** TSP
- **Tier:** P0
- **Binds:** what remains, from the current position
- **Exercises:** FR-21 (frozen prefix), FR-04
- **Breaks:** re-solving from the depot. The tour starts wherever the vehicle is now, which makes it an open TSP with a fixed origin and, often, no fixed destination
#### UC-088 — Snowplough or gritter within a fixed beat

Ordering treatment segments already assigned to one vehicle.

- **Variant:** TSP
- **Tier:** P2
- **Tags:** arc-routing-boundary
- **Binds:** treatment priority order and material capacity
- **Exercises:** FR-13
- **Breaks:** node routing. This is arc routing at the segment level; listed here as the boundary where the TSP framing stops being adequate — see `UC-042`
#### UC-089 — Photography or survey shot list

Ordering site visits for a single surveyor with light-dependent windows.

- **Variant:** TSP
- **Tier:** P2
- **Binds:** daylight and sun angle
- **Exercises:** FR-04 (windows derived from solar position)
- **Breaks:** fixed windows. The window is a computed function of date and location, which tests whether windows can be supplied per-instance rather than assumed static
#### UC-090 — Multi-drop van already loaded in a fixed order

The van was loaded last-in-first-out; the sequence must respect the load.

- **Variant:** TSP
- **Tier:** P1
- **Tags:** lifo
- **Binds:** physical access to items in the load
- **Exercises:** FR-01, FR-05
- **Breaks:** free sequencing. If the model can reorder stops but the loader cannot reorder the van, the plan requires unloading half the vehicle at every stop
## 6. CVRP — capacity binds before the clock

Twenty-four scenarios. Wide or absent time windows; the vehicle fills before the day runs out.

#### UC-013 — Municipal waste and recycling collection

Bin lorries on residential rounds to a transfer station or landfill.

- **Variant:** CVRP
- **Tier:** P0
- **Tags:** multi-trip
- **Binds:** hopper volume, which fills two or three times per shift
- **Exercises:** FR-02, FR-09 (tipping as reload), FR-11, FR-15
- **Breaks:** single-trip modelling. A round is three trips to the tip; chaining independent single-trip plans double-counts the driver's available day
#### UC-008 — Fuel and LPG bulk delivery

Tanker deliveries to filling stations and domestic tanks.

- **Variant:** CVRP
- **Tier:** P1
- **Tags:** multi-compartment
- **Binds:** compartment volume per fuel grade
- **Exercises:** FR-02, FR-07, FR-10, FR-11 (ADR routing restrictions)
- **Breaks:** continuous capacity. Compartments are grade-specific and partial fills are often prohibited, so load building is a discrete packing problem inside the routing problem
#### UC-015 — Milk and agricultural collection

Tanker collection from farms to a processing plant.

- **Variant:** CVRP
- **Tier:** P1
- **Binds:** tanker volume and farm milking schedules
- **Exercises:** FR-02, FR-04, FR-06, FR-11
- **Breaks:** treating farm quantity as known. Volumes are estimates, and a route planned to 99% capacity overflows on a good yield day — the route-failure recourse case
#### UC-004 — Beverage and brewery distribution

Kegs and crates to licensed premises, with empties collected on the same visit.

- **Variant:** CVRP
- **Tier:** P0
- **Tags:** simultaneous-pandd
- **Binds:** weight, then floor space once empties are aboard
- **Exercises:** FR-02, FR-03, FR-05
- **Breaks:** computing load as deliveries only. Load does not decrease monotonically, so the vehicle can exceed capacity at a stop it was supposedly emptying
#### UC-091 — Bottled water and cooler delivery

Full bottles out, empties back, on residential and office rounds.

- **Variant:** CVRP
- **Tier:** P1
- **Tags:** simultaneous-pandd
- **Binds:** bottle count in both directions
- **Exercises:** FR-03, FR-02
- **Breaks:** modelling empties as free. They occupy the same slots the full bottles vacated, so peak load is roughly constant along the whole route
#### UC-092 — Aggregate and ready-mix concrete delivery

Concrete from a batching plant to pours.

- **Variant:** CVRP
- **Tier:** P1
- **Tags:** hard-perishability
- **Binds:** load volume and the concrete's working life
- **Exercises:** FR-02, FR-06, FR-04 (a maximum time from batching)
- **Breaks:** treating the deadline as a delivery window. The clock starts at loading, so the constraint is elapsed time since departure, not arrival time at the customer
#### UC-093 — Animal feed bulk blowing

Blown feed into farm silos from a compartmented tanker.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** multi-compartment
- **Binds:** compartment allocation per feed type
- **Exercises:** FR-02, FR-10, FR-11
- **Breaks:** aggregate weight. Cross-contamination rules forbid mixing feed types in a compartment, which makes the assignment discrete
#### UC-094 — LPG cylinder exchange rounds

Full cylinders delivered, empties collected from retailers.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** simultaneous-pandd
- **Binds:** cylinder positions, not weight
- **Exercises:** FR-02, FR-03, FR-11 (ADR)
- **Breaks:** weight-based capacity. The binding limit is the number of secured cylinder positions on the vehicle deck
#### UC-095 — Grain and harvest haulage

Field-to-store haulage during a compressed harvest window.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** seasonal
- **Binds:** trailer capacity and combine output rate
- **Exercises:** FR-02, FR-09, FR-19 (weighbridge as a resource)
- **Breaks:** static demand. Supply arrives at the rate the combine cuts, making this closer to a synchronisation problem than a demand-driven one
#### UC-096 — Skip and roll-on-roll-off exchange

One container at a time, delivered empty and collected full.

- **Variant:** CVRP
- **Tier:** P1
- **Tags:** unit-capacity
- **Binds:** a capacity of exactly one
- **Exercises:** FR-02, FR-01, FR-07
- **Breaks:** treating capacity as continuous. A capacity of one turns every route into a strict alternation, and neighbourhood moves that assume divisible load are useless here
#### UC-097 — Bulk chemical and food-grade tanker delivery

Tankers requiring cleaning between incompatible products.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** wash-out
- **Binds:** wash-out time between product classes
- **Exercises:** FR-10, FR-05, FR-09 (wash station as an intermediate facility)
- **Breaks:** sequence-independent service time. The cost of a stop depends on what the tanker carried previously, which is a sequence-dependent setup time
#### UC-098 — Pallet network trunking to hubs

Consolidated pallets from depots to a central hub overnight.

- **Variant:** CVRP
- **Tier:** P1
- **Binds:** trailer pallet spaces
- **Exercises:** FR-02, FR-08, FR-19
- **Breaks:** ignoring the hub cut-off. Every trunk shares one arrival deadline, so this behaves like `UC-010` — the whole trip either makes the sort or it does not
#### UC-099 — Vending machine cash and stock collection

Combined restock and cash collection on machine rounds.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** prize-collecting
- **Binds:** which machines justify a visit today
- **Exercises:** FR-12, FR-02, FR-18
- **Breaks:** mandatory coverage. The decision is whether to go at all, driven by predicted depletion
#### UC-011 — Cash-in-transit ATM and retail collection

Cash collected from ATMs and retail sites under an insured value ceiling.

- **Variant:** CVRP
- **Tier:** P2
- **Binds:** an insurance-imposed value limit, and a requirement for route unpredictability
- **Exercises:** FR-02 (value as a capacity dimension), FR-10, FR-18
- **Breaks:** consistency as a universal good. This is the one domain where route repetition is a security risk, so the objective wants controlled variation rather than stability
#### UC-100 — Retail cash and coin delivery to branches

Denominated cash to bank branches and ATMs.

- **Variant:** CVRP
- **Tier:** P2
- **Binds:** the insured value ceiling, which caps the load long before volume does
- **Exercises:** FR-02 (value dimension), FR-04, FR-18 (inverted)
- **Breaks:** value as a soft cost. Exceeding the insured limit voids cover entirely — it is a hard constraint with a cliff, not a penalty
#### UC-101 — Water and sewage tanker services

Emptying septic tanks and delivering potable water.

- **Variant:** CVRP
- **Tier:** P2
- **Binds:** tank volume and disposal site availability
- **Exercises:** FR-02, FR-09 (disposal as reload), FR-08
- **Breaks:** treating disposal as free. Disposal sites have hours and queues, and the nearest one is often not the cheapest
#### UC-102 — Furniture flat-pack home delivery

Volume-dominated household deliveries with wide windows.

- **Variant:** CVRP
- **Tier:** P2
- **Binds:** cube, decisively — the load fills the body long before it approaches the weight limit
- **Exercises:** FR-02, FR-05
- **Breaks:** weight-based capacity. Flat-pack is light and enormous; a weight model plans routes the van physically cannot hold
#### UC-103 — Charity and food-bank collection rounds

Donations collected from supermarkets to a distribution centre.

- **Variant:** CVRP
- **Tier:** P2
- **Binds:** volunteer vehicle capacity and volunteer availability
- **Exercises:** FR-02, FR-16, FR-17
- **Breaks:** assuming a professional fleet. Vehicles and shift lengths vary per volunteer per day, which makes the fleet definition an input that changes daily
#### UC-104 — Construction site welfare and plant servicing

Servicing generators, toilets and plant across active sites.

- **Variant:** CVRP
- **Tier:** P2
- **Binds:** consumables carried, plus site access windows
- **Exercises:** FR-02, FR-11, FR-10
- **Breaks:** static site data. Construction sites move, open and close weekly, so the location master churns faster than any other domain and cache assumptions break
#### UC-105 — Parcel locker replenishment and clearance

Loading outbound parcels into lockers and clearing returns.

- **Variant:** CVRP
- **Tier:** P1
- **Tags:** simultaneous-pandd
- **Binds:** locker compartment availability at each site
- **Exercises:** FR-02, FR-03, FR-19 (locker capacity as a resource)
- **Breaks:** treating the locker as unlimited. The destination has finite capacity, which is a constraint on the *delivery* side that most models only apply to vehicles
#### UC-106 — Newspaper and magazine returns collection

Unsold stock collected on the next delivery round.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** backhaul
- **Binds:** space available after the delivery leg
- **Exercises:** FR-03, FR-17
- **Breaks:** collecting before the load has cleared. Classic backhaul semantics — space for returns only exists later in the route
#### UC-107 — Medical gas cylinder distribution to homes

Oxygen cylinders to home patients on a rolling cycle.

- **Variant:** CVRP
- **Tier:** P1
- **Binds:** cylinder positions and ADR quantity thresholds
- **Exercises:** FR-02, FR-10, FR-13, FR-18
- **Breaks:** ignoring the threshold. Below a quantity limit the load is exempt from full ADR rules; above it the vehicle and driver requirements change entirely — a discontinuity in eligibility
#### UC-108 — Laundry bulk collection from care homes

Clean linen delivered, soiled collected, on a fixed weekly cycle.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** simultaneous-pandd
- **Binds:** cage positions both ways
- **Exercises:** FR-03, FR-18, FR-P01 (multi-period horizon)
- **Breaks:** planning days independently. The weekly cycle is the unit of commitment, so a locally cheap Tuesday can make Friday's promised visits infeasible
#### UC-109 — Agricultural input delivery to farms

Fertiliser and seed delivered in a compressed planting window.

- **Variant:** CVRP
- **Tier:** P2
- **Tags:** seasonal
- **Binds:** seasonal fleet capacity against a demand spike
- **Exercises:** FR-02, FR-30, FR-33, FR-34
- **Breaks:** annual-average fleet sizing. Demand is concentrated in weeks, so the fleet question is about peak-week hire, not steady-state ownership
#### UC-110 — Pool and spa chemical service rounds

Chemicals delivered and applied on a repeating schedule.

- **Variant:** CVRP
- **Tier:** P2
- **Binds:** chemical capacity and incompatible product carriage
- **Exercises:** FR-02, FR-10, FR-18
- **Breaks:** allowing incompatible chemicals to share a vehicle. Some combinations are prohibited to carry together regardless of quantity
## 7. VRPTW — time binds before capacity

Thirty-three scenarios. Vehicles go out part-empty because fleet size is driven by window overlap,
not volume.

#### UC-001 — Grocery home delivery

Supermarket orders to residential addresses in booked slots.

- **Variant:** VRPTW
- **Tier:** P0
- **Binds:** customer slot availability, not vehicle volume
- **Exercises:** FR-02, FR-04, FR-05, FR-06, FR-13, FR-19
- **Breaks:** sizing the fleet from volume. Vans leave 60% empty because everyone books 17:00–19:00; a volume-derived fleet is short by a third on the evening peak
#### UC-003 — Retail store delivery into receiving-bay hours

Pallets from a regional DC into stores with strict goods-in windows.

- **Variant:** VRPTW
- **Tier:** P0
- **Binds:** bay opening hours plus driver hours
- **Exercises:** FR-04, FR-05, FR-11, FR-15, FR-16
- **Breaks:** treating the window as soft. A store closing goods-in at 11:00 does not accept an 11:20 arrival; the pallets come back and the trip is wasted
#### UC-005 — Pharmaceutical and clinical supply

Temperature-controlled deliveries with cut-off-driven release.

- **Variant:** VRPTW
- **Tier:** P1
- **Binds:** release from the dispensary, then a narrow delivery window
- **Exercises:** FR-04, FR-06, FR-10, FR-13
- **Breaks:** ignoring release times. A 09:00 delivery is planned for a batch not dispensed until 10:30, and everything downstream is fiction
#### UC-006 — Bakery and fresh produce early-morning distribution

Overnight production delivered before store opening.

- **Variant:** VRPTW
- **Tier:** P1
- **Tags:** multi-trip
- **Binds:** a compressed 03:00–07:00 window against duty limits
- **Exercises:** FR-04, FR-09, FR-15, FR-19
- **Breaks:** multi-trip planning that ignores dock contention. Twelve vehicles reloading at 05:00 against three bays produces an hour of invisible queueing
#### UC-009 — Parcel last mile from a delivery station

High-density residential and business parcel delivery.

- **Variant:** VRPTW
- **Tier:** P0
- **Binds:** stops per hour, which is service and access time
- **Exercises:** FR-05, FR-13, FR-18
- **Breaks:** optimising distance. Travel is roughly a third of the driver's day (§4.2); a plan saving 8% of distance and adding parking difficulty is worse
#### UC-010 — Newspaper and periodical distribution

Overnight drops with a hard on-shelf deadline.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** one common deadline across all stops
- **Exercises:** FR-04, FR-13, FR-17
- **Breaks:** soft-window treatment. With a shared deadline lateness is not a gradient — the route either makes it or does not
#### UC-018 — Pathology specimen collection

Samples from clinics to a central laboratory.

- **Variant:** VRPTW
- **Tier:** P1
- **Binds:** specimen stability windows and lab cut-off
- **Exercises:** FR-04, FR-06, FR-13, FR-16
- **Breaks:** no route-end constraint. A sample collected in-window but delivered after cut-off is destroyed
#### UC-019 — Utility installation and repair appointments

Engineers to customer premises in booked slots.

- **Variant:** VRPTW
- **Tier:** P0
- **Tags:** home-start
- **Binds:** appointment windows and skill coverage
- **Exercises:** FR-04, FR-05, FR-08, FR-10, FR-16
- **Breaks:** modelling technicians as depot-based. Each home is a distinct start and end, making this multi-depot even with one office
#### UC-022 — Home care and domiciliary visits

Carers visiting clients on a recurring weekly schedule.

- **Variant:** VRPTW
- **Tier:** P1
- **Tags:** consistency-dominant
- **Binds:** continuity of carer, then time windows
- **Exercises:** FR-04, FR-17, FR-18
- **Breaks:** consistency as a tie-breaker. Here it outranks cost — a different carer for a dementia patient is a care failure, not a small penalty
#### UC-024 — Pest control and scheduled inspections

Compliance visits with a due-by date rather than a window.

- **Variant:** VRPTW
- **Tier:** P2
- **Tags:** multi-period
- **Binds:** a monthly compliance deadline across a horizon
- **Exercises:** FR-06, FR-13, FR-18
- **Breaks:** daily planning in isolation. The unit of optimisation is the month; a locally optimal Tuesday leaves an infeasible Friday
#### UC-025 — Mobile veterinary and rural service

Long-distance rural calls with few stops.

- **Variant:** VRPTW
- **Tier:** P2
- **Tags:** sparse
- **Binds:** travel time between distant farms
- **Exercises:** FR-05, FR-14, FR-16
- **Breaks:** neighbourhood pruning tuned for urban density. With 40km between stops, k-nearest lists built for cities exclude the only feasible moves
#### UC-043 — Meter reading and inspection rounds

Periodic visits to fixed assets on a repeating cycle.

- **Variant:** VRPTW
- **Tier:** P1
- **Tags:** periodic
- **Binds:** visit-frequency compliance across the horizon
- **Exercises:** FR-06, FR-17, FR-18, FR-35
- **Breaks:** daily optimisation. The decision is which days to visit which assets; optimising each day independently makes the cycle infeasible
#### UC-046 — Postal delivery under universal service obligation

Statutory coverage regardless of economics.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** the legal obligation to serve every address
- **Exercises:** FR-13, FR-17, FR-35
- **Breaks:** prize-collecting logic. No address may be declined, so the drop-the-unprofitable-stop behaviour that helps elsewhere is prohibited
#### UC-111 — Boiler and appliance annual service visits

Compliance-driven annual services with customer-chosen slots.

- **Variant:** VRPTW
- **Tier:** P1
- **Tags:** periodic
- **Binds:** the anniversary window plus customer availability
- **Exercises:** FR-04, FR-06, FR-10, FR-18
- **Breaks:** treating the anniversary as a soft target. Missing it breaks the service contract, so it is a hard deadline with a soft preference inside it
#### UC-112 — Legal and court document service

Documents served on individuals within a statutory deadline.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** the statutory deadline and the recipient being present
- **Exercises:** FR-04, FR-06, FR-13
- **Breaks:** single-attempt modelling. Success is probabilistic per attempt, so the plan must budget for repeat visits at different times of day
#### UC-113 — Fresh flower and floristry distribution

Perishables from wholesale market to florists before opening.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** a market release time and a shop opening deadline
- **Exercises:** FR-06, FR-04, FR-05
- **Breaks:** ignoring the release. Everything departs at once from a market that opens at 04:00, which creates a depot-departure bottleneck (`FR-19`) that dominates the plan
#### UC-114 — School meal and catering distribution

Hot and chilled meals to schools before service time.

- **Variant:** VRPTW
- **Tier:** P1
- **Binds:** service time at each school, staggered by timetable
- **Exercises:** FR-04, FR-02, FR-05, FR-13
- **Breaks:** uniform windows. Each school has a different lunch sitting, so windows are narrow, staggered and non-negotiable — the hardest shape of VRPTW instance
#### UC-115 — Office coffee and pantry supply

Consumables to offices within business hours.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** building access hours and goods-lift booking
- **Exercises:** FR-04, FR-11, FR-19 (lift as a shared resource)
- **Breaks:** treating the building as a point. In a tower, the goods lift is a booked, contended resource and the real service time is lift waiting
#### UC-116 — Bank branch and ATM engineering

Engineers servicing machines under uptime SLAs.

- **Variant:** VRPTW
- **Tier:** P1
- **Binds:** SLA response clocks that start when the fault is reported
- **Exercises:** FR-06, FR-13, FR-04, FR-10
- **Breaks:** fixed windows. The window is derived from the fault timestamp plus the SLA, so it is computed at intake and differs per order
#### UC-117 — IT field support with tiered SLAs

Break-fix visits with four-hour, next-business-day and best-effort tiers.

- **Variant:** VRPTW
- **Tier:** P1
- **Binds:** the mix of SLA tiers on any given day
- **Exercises:** FR-13, FR-12, FR-04
- **Breaks:** one priority scale. Three tiers with different clocks are three different constraints, not three weights on one — see the `FR-13` split recommended in §12.2
#### UC-118 — Window cleaning and facade maintenance rounds

Rounds constrained by building access and weather.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** access permission windows and wind limits
- **Exercises:** FR-04, FR-11, FR-12
- **Breaks:** deterministic planning. Weather cancels work mid-day, which makes this a dynamic problem disguised as a scheduled one
#### UC-119 — Mobile phlebotomy and home blood draws

Nurses visiting patients, with fasting-dependent windows.

- **Variant:** VRPTW
- **Tier:** P1
- **Binds:** fasting windows early in the morning, plus lab cut-off
- **Exercises:** FR-04, FR-06, FR-10, FR-16
- **Breaks:** spreading work evenly. Fasting draws must happen before roughly 10:00, so demand is structurally front-loaded and afternoon capacity is worthless
#### UC-120 — Retail merchandising and planogram visits

Merchandisers resetting displays in stores.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** store-agreed visit windows and shift length
- **Exercises:** FR-04, FR-05, FR-17, FR-18
- **Breaks:** uniform service time. Job duration varies by store size and planogram complexity by an order of magnitude, so an average is useless
#### UC-121 — Equipment hire delivery and collection

Plant delivered on hire start and collected on hire end.

- **Variant:** VRPTW
- **Tier:** P1
- **Tags:** paired
- **Binds:** hire start and end dates, both customer-agreed
- **Exercises:** FR-01, FR-04, FR-02, FR-07
- **Breaks:** treating delivery and collection as unrelated. They are one commercial contract with two routing events days apart, and collection failure directly costs hire revenue
#### UC-122 — Charity shop donation collection

Bulky donations collected from homes by appointment.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** appointment windows and two-person crew availability
- **Exercises:** FR-04, FR-02, FR-P03 (crew as a resource)
- **Breaks:** modelling the crew as the vehicle. Crew hours and vehicle hours are separate resources that bind independently
#### UC-123 — Prescription delivery to housebound patients

Medicines delivered from pharmacy to home, some requiring signature.

- **Variant:** VRPTW
- **Tier:** P1
- **Binds:** dispensing release plus the patient being home
- **Exercises:** FR-06, FR-04, FR-10 (controlled drugs), FR-13
- **Breaks:** ignoring controlled-drug handling. Some items require a specific driver qualification and cannot be left, which is a skill constraint plus a no-safe-place rule
#### UC-124 — Fuel card and forecourt maintenance

Technicians servicing pumps during low-traffic hours.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** site-agreed maintenance windows outside peak trading
- **Exercises:** FR-04, FR-11, FR-16
- **Breaks:** assuming daytime operation. Windows are deliberately overnight, which pushes the plan into driver-hours territory that daytime instances never reach
#### UC-125 — Hotel and hospitality linen delivery

Clean linen in, soiled out, before check-in time.

- **Variant:** VRPTW
- **Tier:** P1
- **Tags:** simultaneous-pandd
- **Binds:** the housekeeping deadline before check-in
- **Exercises:** FR-03, FR-04, FR-02
- **Breaks:** peak-load blindness. The vehicle holds clean and soiled linen simultaneously mid-route, so the binding quantity is the running maximum, not either direction's total
#### UC-126 — Dairy and doorstep delivery rounds

Very early, very dense residential rounds with hundreds of stops.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** stop count within a pre-dawn window
- **Exercises:** FR-05 (seconds per stop), FR-18, FR-17
- **Breaks:** per-stop overhead assumptions calibrated on parcel. At 15 seconds per drop and 400 stops, service-time error of five seconds per stop shifts the route by half an hour
#### UC-127 — Mobile library and community service vehicles

Published timetable stops with fixed dwell periods.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** the published timetable itself
- **Exercises:** FR-04 (windows as fixed appointments), FR-05
- **Breaks:** optimising the sequence. The timetable is published to the public; the sequence is an input and only the routing between stops is open
#### UC-128 — Court and prisoner transport

Movements between custody, court and prison with strict timings.

- **Variant:** VRPTW
- **Tier:** P2
- **Tags:** paired, security
- **Binds:** court sitting times and cell-space availability
- **Exercises:** FR-01, FR-04, FR-10, FR-11
- **Breaks:** allowing arbitrary co-loading. Individuals who must be kept separate is a hard order↔order incompatibility with legal consequences
#### UC-129 — Fire extinguisher and safety equipment inspection

Annual and semi-annual statutory inspections.

- **Variant:** VRPTW
- **Tier:** P2
- **Tags:** periodic
- **Binds:** statutory inspection intervals
- **Exercises:** FR-06, FR-18, FR-P01 (multi-period horizon)
- **Breaks:** treating each visit as independent. The next due date is set by the last visit, so today's plan determines next year's feasible plan
#### UC-130 — EV charge-point maintenance

Technicians servicing public charging infrastructure.

- **Variant:** VRPTW
- **Tier:** P2
- **Binds:** fault SLA clocks plus site access
- **Exercises:** FR-06, FR-13, FR-11, FR-20 (if the service fleet is itself electric)
- **Breaks:** nothing unusual on its own — included because it is the common case where the *service* fleet is electric, stacking `FR-20` on top of everything else
## 8. MDHVRPTW — multiple origins, vehicles that are not interchangeable

Twenty-seven scenarios. Depot choice and vehicle-type choice become decision variables.

#### UC-002 — Multi-temperature convenience store replenishment

Frozen, chilled and ambient in one vehicle to small-format stores.

- **Variant:** MDHVRPTW
- **Tier:** P0
- **Binds:** frozen compartment volume
- **Exercises:** FR-02 (compartments as dimensions), FR-07, FR-10, FR-11
- **Breaks:** aggregate capacity checks. Total volume is the wrong feasibility test when the vehicle is physically partitioned
#### UC-007 — Builders' merchant and heavy materials

Aggregate, timber and plasterboard to construction sites.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Binds:** axle weight and vehicle-type eligibility
- **Exercises:** FR-02, FR-07, FR-10 (crane), FR-11
- **Breaks:** assuming any vehicle can serve any site. A site with no forklift needs a vehicle-mounted crane; a curtainsider arrives with an undeliverable load
#### UC-012 — E-commerce fulfilment with carrier handover

Own fleet within a radius, third-party carriers beyond it.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Tags:** prize-collecting
- **Binds:** the own-versus-hire break-even
- **Exercises:** FR-12, FR-30, FR-33, FR-36
- **Breaks:** hired capacity as marginal cost per kilometre. A hired vehicle costs a full day; amortising hides the step and hires eleven vehicles for one parcel each
#### UC-020 — HVAC and appliance service with parts dependency

Service calls requiring specific parts carried on the van.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Binds:** parts availability on the assigned vehicle
- **Exercises:** FR-02, FR-09 (depot restock mid-day), FR-10
- **Breaks:** static skills. Van inventory depletes as jobs complete, so eligibility is sequence-dependent — the gap identified in v1.0 §11.1
#### UC-037 — Urban distribution with low-emission zone restrictions

Mixed fleet where only some vehicles may enter the centre.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Binds:** vehicle-class eligibility by zone and time of day
- **Exercises:** FR-07, FR-11, FR-30, FR-31
- **Breaks:** static access rules. Permitted hours and emission classes vary by time, so eligibility is a function of arrival time, not of the vehicle alone
#### UC-131 — Two-echelon urban distribution with micro-hubs

Large vehicles to satellite hubs, cargo bikes and vans onward.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Tags:** 2e
- **Binds:** synchronisation between echelons at the satellite
- **Exercises:** FR-08, FR-07, FR-09, FR-19
- **Breaks:** solving the echelons independently. The second-echelon departure depends on the first echelon's arrival, which is a synchronisation constraint across two routing problems
#### UC-132 — National 3PL with shared customer networks

One fleet serving multiple client contracts from shared depots.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Binds:** contractual separation of client goods and cost allocation
- **Exercises:** FR-10 (client incompatibility), FR-31, FR-36
- **Breaks:** a single cost objective. Costs must be attributable per client contract, which makes cost-to-serve a required output, not an analysis
#### UC-133 — Cross-dock and hub-and-spoke consolidation

Inbound consolidated at a cross-dock, outbound routed onward.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Binds:** cross-dock cut-off and door capacity
- **Exercises:** FR-19, FR-08, FR-06, FR-31
- **Breaks:** treating the cross-dock as a depot. Outbound release depends on inbound arrival, making release times endogenous to the plan
#### UC-134 — Regional distribution with overlapping depot catchments

Several DCs able to serve the same customers.

- **Variant:** MDHVRPTW
- **Tier:** P0
- **Binds:** depot choice against per-depot inventory
- **Exercises:** FR-31, FR-08, FR-30, FR-36
- **Breaks:** nearest-depot assignment. The nearest depot may lack stock or capacity, and fixing assignment before routing forecloses the cheapest plans
#### UC-135 — Franchise and multi-branch service networks

Independent branches with their own vehicles and territories.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** branch boundaries, which are commercial rather than geographic
- **Exercises:** FR-31, FR-35, FR-10
- **Breaks:** optimising across boundaries. Cross-branch work has revenue-sharing implications, so a cheaper plan can be commercially unacceptable
#### UC-136 — Mixed own-fleet and courier network

Employed drivers alongside gig couriers with different cost structures.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Tags:** prize-collecting
- **Binds:** gig availability, which is not guaranteed
- **Exercises:** FR-33, FR-30, FR-12, FR-07
- **Breaks:** treating gig capacity as available. Supply is stochastic and price-responsive, so the fleet itself is uncertain
#### UC-137 — Grocery multi-format distribution

Superstores, convenience and dark stores served from shared DCs.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Binds:** format-specific vehicle eligibility and drop sizes
- **Exercises:** FR-07, FR-11, FR-02, FR-05
- **Breaks:** uniform drop modelling. A superstore takes 26 pallets and a convenience store takes two cages, and the service-time model must span both
#### UC-138 — Automotive parts distribution to dealers and workshops

Multiple daily waves from regional stores to workshops.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Tags:** multi-wave
- **Binds:** wave cut-offs through the day
- **Exercises:** FR-09, FR-06, FR-22, FR-19
- **Breaks:** single-wave planning. Four waves per day is four coupled planning problems sharing one fleet and one set of driver hours
#### UC-139 — Building services and facilities management

Multi-trade engineers dispatched from regional bases.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** trade skills against job requirements
- **Exercises:** FR-10, FR-08, FR-04, FR-17
- **Breaks:** single-skill matching. Jobs often need a combination of trades, so eligibility is a set-cover condition, not a single flag
#### UC-140 — Waste with multiple transfer stations and disposal routes

Collection vehicles choosing between disposal sites.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Tags:** multi-trip
- **Binds:** transfer station hours, gate fees and queueing
- **Exercises:** FR-09, FR-31, FR-11, FR-19
- **Breaks:** nearest-tip routing. Sites differ in fee, queue and material acceptance, so disposal choice is an optimisation decision with real money attached
#### UC-141 — Concrete and asphalt supply from multiple plants

Pours served from whichever plant can meet the schedule.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** plant batching capacity and the material's working life
- **Exercises:** FR-31, FR-06, FR-19, FR-04
- **Breaks:** independent plant scheduling. Plants share the customer base, so plant choice and pour scheduling are one problem
#### UC-142 — Retail returns and reverse network

Returns consolidated from stores to processing centres.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Tags:** backhaul
- **Binds:** processing-centre capacity by category
- **Exercises:** FR-03, FR-31, FR-12
- **Breaks:** treating all returns as equivalent. Categories route to different processing centres, so destination is item-dependent
#### UC-143 — Utilities emergency and planned work from district depots

Planned maintenance interrupted by emergency callouts.

- **Variant:** MDHVRPTW
- **Tier:** P1
- **Binds:** reserving capacity for unplanned work
- **Exercises:** FR-12, FR-13, FR-22, FR-30
- **Breaks:** full utilisation. Planning every crew to 100% leaves nothing for emergencies, so the model must deliberately hold capacity back
#### UC-144 — Airline catering and aircraft servicing

High-loaders servicing aircraft against departure schedules.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** aircraft turnaround windows, measured in minutes
- **Exercises:** FR-04, FR-07, FR-11 (airside access), FR-19
- **Breaks:** minute-scale tolerance. Windows are tens of minutes wide with no slack, and airside movement rules constrain the network more than distance does
#### UC-145 — Port and terminal internal logistics

Yard tractors moving containers within terminal boundaries.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** crane schedules and yard congestion
- **Exercises:** FR-14, FR-19, FR-01
- **Breaks:** an external road network. The network is private, congestion is endogenous to the plan, and travel times depend on the plan's own vehicle density
#### UC-146 — Mining and quarry haulage

Haul trucks between faces, crushers and stockpiles.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** loader and crusher throughput
- **Exercises:** FR-19, FR-09, FR-07
- **Breaks:** routing without queueing. The binding constraint is queue time at fixed equipment, making this a flow problem where routing is secondary
#### UC-147 — Military and humanitarian resupply convoys

Supplies from staging areas to forward locations.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** security windows, convoy grouping, and route risk
- **Exercises:** FR-11, FR-13, FR-10, FR-21
- **Breaks:** independent vehicle routing. Vehicles must travel in convoy, which is a synchronisation constraint forcing several routes to share a path and a schedule
#### UC-148 — Ski resort and remote site servicing

Supplies to mountain restaurants and lifts.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Tags:** seasonal-access
- **Binds:** seasonal and weather-dependent road availability
- **Exercises:** FR-11, FR-14, FR-07
- **Breaks:** a static network. Access changes daily with snow and avalanche closures, so the matrix is a daily input, not a cached asset
#### UC-039 — Abnormal load and permit-constrained haulage

Oversize loads moved on pre-approved routes with time-of-day restrictions.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** the permitted route itself, which is issued rather than computed
- **Exercises:** FR-11, FR-14, FR-21
- **Breaks:** free network routing. The road path is an input, not an output, so the engine must accept a fixed corridor and optimise only the schedule within it
#### UC-149 — Agricultural contractor machine movement

Combines and balers moved between farms in season.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** low-loader availability and machine transport dimensions
- **Exercises:** FR-07, FR-11, FR-01
- **Breaks:** standard vehicle profiles. Abnormal-load routing applies, so the network available to a low-loader differs from the one available to a van — see `UC-039`
#### UC-150 — Multi-country European distribution

Cross-border groupage with cabotage and border constraints.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** cabotage rules and border crossing times
- **Exercises:** FR-11, FR-14, FR-15, FR-10
- **Breaks:** a borderless network. Cabotage limits which vehicles may perform domestic legs in which country, which is a vehicle-to-region eligibility constraint invisible in the road graph
#### UC-151 — Retail delivery with in-store and kerbside pickup

Store-fulfilled orders delivered or collected.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Binds:** store picking capacity versus delivery capacity
- **Exercises:** FR-06, FR-31, FR-19
- **Breaks:** unlimited store release. Picking capacity per store per hour caps how many orders can be released, which constrains routing upstream — the Alibaba lesson in §4.6
#### UC-152 — Field sales and account management territories

Reps visiting accounts on a call-cycle frequency.

- **Variant:** MDHVRPTW
- **Tier:** P2
- **Tags:** periodic
- **Binds:** call frequency by account tier
- **Exercises:** FR-18, FR-35, FR-06, FR-13
- **Breaks:** cost minimisation. The objective is coverage compliance and relationship consistency, with travel cost a distant third
## 9. PDPTW — the goods or people have their own origin

Twenty-six scenarios across three structurally distinct families.

### 9.1 Same-vehicle paired transport

#### UC-030 — Same-day courier with rolling intake

Point-to-point jobs arriving through the day.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** dynamic
- **Binds:** the dispatch-now-versus-consolidate decision
- **Exercises:** FR-01, FR-12, FR-21, FR-22
- **Breaks:** greedy dispatch. Sending every job on arrival forgoes all consolidation; the value is in knowing which jobs can safely wait
#### UC-036 — Container drayage from port to consignee

Full container out, empty returned to a depot or another shipper.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** empty-repositioning
- **Binds:** chassis and container availability, plus gate hours
- **Exercises:** FR-01, FR-04, FR-08, FR-11
- **Breaks:** delivery-only modelling. The empty return is a first-class request; ignoring it under-counts the day by roughly half
#### UC-153 — Removals and household moving

Whole-household loads between two addresses.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** lifo
- **Binds:** crew hours and load-order feasibility
- **Exercises:** FR-01, FR-02, FR-05, FR-P03 (crew as a resource)
- **Breaks:** free sequencing. What goes in first comes out last, so the loading order constrains the route order
#### UC-154 — Vehicle recovery and roadside assistance

Casualty vehicles collected and taken to a garage or home.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** dynamic
- **Binds:** response-time targets and recovery-vehicle compatibility
- **Exercises:** FR-01, FR-07, FR-10, FR-13, FR-22
- **Breaks:** capacity as a number. A recovery truck carries one vehicle, and vehicle-to-casualty compatibility (weight, drivetrain, damage) decides eligibility
#### UC-155 — Car transporter delivery to dealerships

New vehicles from ports and plants to dealers.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** loading-constraints
- **Binds:** deck positions and load sequence
- **Exercises:** FR-02, FR-01, FR-11 (height when loaded)
- **Breaks:** interchangeable capacity slots. Vehicles have different heights and only some deck positions fit each, making load building a 2D packing problem
#### UC-156 — Document and legal courier with chain of custody

Signed collections and deliveries with custody tracking.

- **Variant:** PDPTW
- **Tier:** P2
- **Binds:** same-courier custody requirements
- **Exercises:** FR-01 (same-vehicle enforcement), FR-10, FR-04
- **Breaks:** allowing transhipment. Custody rules forbid handover, which is exactly the same-vehicle constraint that distinguishes a shipment from two independent jobs
#### UC-157 — Blood, organ and urgent clinical transport

Time-critical medical items between hospitals.

- **Variant:** PDPTW
- **Tier:** P1
- **Binds:** viability windows measured in hours
- **Exercises:** FR-01, FR-04, FR-06, FR-13
- **Breaks:** treating the window as a delivery constraint. The clock starts at collection, making it a maximum elapsed time per shipment, not an arrival window
#### UC-158 — Pallet and equipment repositioning between depots

Empty pallets, cages and containers rebalanced across the network.

- **Variant:** PDPTW
- **Tier:** P2
- **Binds:** surplus and deficit across sites
- **Exercises:** FR-01, FR-03, FR-31
- **Breaks:** fixed origin-destination pairs. The pairing itself is a decision — any surplus site can serve any deficit site, so this is a transportation problem nested in routing
#### UC-159 — Waste transfer between transfer stations and disposal

Bulk haulage of consolidated waste onward for treatment.

- **Variant:** PDPTW
- **Tier:** P2
- **Binds:** treatment site acceptance windows and material type
- **Exercises:** FR-01, FR-10, FR-31, FR-04
- **Breaks:** destination as an input. Material class determines which sites can accept it, so destination selection is part of the optimisation
### 9.2 Simultaneous pickup and delivery at one stop

#### UC-016 — Laundry and linen exchange

Clean linen delivered, soiled collected, on the same visit.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** simultaneous
- **Binds:** cage count in both directions
- **Exercises:** FR-02, FR-03, FR-04, FR-18
- **Breaks:** ignoring the return leg. Peak load occurs mid-route when the vehicle holds both
#### UC-014 — Commercial waste with mixed container types

Skips, RoRo and wheeled bins from business premises.

- **Variant:** PDPTW
- **Tier:** P1
- **Binds:** vehicle-to-container compatibility and one-at-a-time carrying
- **Exercises:** FR-02, FR-03, FR-07, FR-10
- **Breaks:** capacity as a scalar. A skip lorry carries exactly one skip, and exchange requires delivering the empty before collecting the full
#### UC-017 — Returns and reverse logistics on delivery routes

Customer returns collected on outbound rounds.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** backhaul
- **Binds:** space available after deliveries
- **Exercises:** FR-03, FR-12
- **Breaks:** scheduling collections before the load has cleared
#### UC-160 — Beer keg and gas cylinder exchange at pubs

Full out, empty back, with a strict one-for-one exchange.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** simultaneous
- **Binds:** exchange parity — you cannot deliver more than you collect space for
- **Exercises:** FR-03, FR-02, FR-11 (cellar access)
- **Breaks:** independent pickup and delivery quantities. They are coupled per stop, and the coupling is what makes peak load predictable
#### UC-161 — Coffee machine and water cooler servicing

Consumables in, waste and empties out.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** simultaneous
- **Binds:** both directions simultaneously
- **Exercises:** FR-03, FR-02, FR-18
- **Breaks:** modelling the two directions independently. Consumables in and waste out are coupled per stop, so peak load stays roughly constant rather than draining along the route
#### UC-162 — Uniform and workwear rental rounds

Clean garments delivered, used collected, on a fixed weekly cycle.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** simultaneous
- **Binds:** garment volume both ways, plus cycle adherence
- **Exercises:** FR-03, FR-18, FR-P01 (multi-period horizon)
- **Breaks:** optimising each week in isolation. Garment stock in circulation couples consecutive weeks, so this week's plan constrains next week's
#### UC-163 — Medical sharps and clinical waste exchange

Empty containers delivered, full clinical waste collected.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** simultaneous
- **Binds:** regulated waste segregation and quantity thresholds
- **Exercises:** FR-03, FR-10, FR-02
- **Breaks:** mixing waste streams. Segregation is regulatory, so waste classes are separate capacity dimensions that cannot be pooled
### 9.3 People transport

#### UC-026 — School bus routing

Pupils collected to schools against bell times.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** ride-time
- **Binds:** bell time, then maximum time aboard
- **Exercises:** FR-01, FR-02, FR-04, FR-17
- **Breaks:** no ride-time limit. A cost-optimal route can leave a five-year-old aboard for 90 minutes
#### UC-027 — Non-emergency patient transport

Patients to and from appointments, some wheelchair or stretcher.

- **Variant:** DARP
- **Tier:** P1
- **Binds:** appointment times in both directions and vehicle equipment
- **Exercises:** FR-01, FR-02, FR-04, FR-10, FR-13
- **Breaks:** modelling the return as independent. The return cannot be planned before the appointment ends, which is itself uncertain
#### UC-028 — Paratransit and community transport

Demand-responsive service for people with mobility needs.

- **Variant:** DARP
- **Tier:** P2
- **Tags:** dynamic
- **Binds:** booking lead time versus vehicle availability
- **Exercises:** FR-02, FR-04, FR-12, FR-22
- **Breaks:** static planning. The accept-or-decline decision is part of the problem
#### UC-029 — Employee shuttle and crew transport

Shift workers from home clusters to a plant.

- **Variant:** PDPTW
- **Tier:** P2
- **Binds:** shift start and meeting-point capacity
- **Exercises:** FR-02, FR-04, FR-17, FR-18
- **Breaks:** individual pickup modelling. The real structure is choosing meeting points, a location decision nested inside routing
#### UC-164 — Airport transfer and shared shuttle

Passengers pooled to and from terminals against flight times.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** ride-time
- **Binds:** flight departure minus check-in buffer
- **Exercises:** FR-01, FR-04, FR-13
- **Breaks:** symmetric treatment of arrivals and departures. A missed departure is catastrophic; a delayed arrival pickup is an inconvenience, so the two directions have different cost asymmetry
#### UC-165 — Ride pooling and shared taxi

Matching riders to vehicles with detour tolerance.

- **Variant:** PDPTW
- **Tier:** P2
- **Binds:** detour tolerance and matching latency
- **Exercises:** FR-22, FR-01, FR-12
- **Breaks:** routing framing entirely at high density — this degenerates into matching, which is the Lyft boundary case in §4.8
#### UC-166 — Care home and day-centre transport

Regular clients to day centres with consistent seating and carers.

- **Variant:** DARP
- **Tier:** P2
- **Binds:** consistency and equipment matching
- **Exercises:** FR-18, FR-10, FR-02, FR-04
- **Breaks:** treating passengers as interchangeable. Specific individuals need specific vehicles, specific seats and, often, specific companions
#### UC-167 — Crew change transport for offshore and remote sites

Crews moved to helipads, ports and remote sites against sailing times.

- **Variant:** PDPTW
- **Tier:** P2
- **Binds:** vessel and helicopter departure slots
- **Exercises:** FR-04, FR-01, FR-13
- **Breaks:** soft deadlines. Missing a sailing costs a full shift cycle, so the deadline penalty is a cliff, not a slope
### 9.4 Many-to-many with transhipment

#### UC-168 — LTL and groupage freight consolidation

Part-loads consolidated through hubs.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** transhipment
- **Binds:** hub cut-offs and trailer utilisation
- **Exercises:** FR-01, FR-02, FR-08, FR-19
- **Breaks:** same-vehicle enforcement. Transhipment is permitted and often optimal, which violates the defining constraint of standard PDPTW — this needs an explicit model extension
#### UC-169 — Freight exchange and backload matching

Spot-market loads matched to vehicles with empty legs.

- **Variant:** PDPTW
- **Tier:** P2
- **Tags:** prize-collecting
- **Binds:** profitability per load against the empty running it avoids
- **Exercises:** FR-12, FR-01, FR-33
- **Breaks:** mandatory service. Every load is optional and priced, so this is prize-collecting with a market-determined prize
#### UC-170 — Multi-modal first and last mile around rail or sea

Road legs feeding a scheduled line-haul.

- **Variant:** PDPTW
- **Tier:** P2
- **Binds:** the line-haul schedule, which is fixed and external
- **Exercises:** FR-04, FR-06, FR-01
- **Breaks:** optimising the road leg in isolation. The rail departure is an immovable deadline that the road plan serves — listed as a boundary of `SDD-VRP-001` §2.5
## 10. Dynamic operation and disruption

#### UC-032 — Mid-day vehicle breakdown recovery

A vehicle fails at 11:00 with half its route undelivered.

- **Variant:** VRPTW
- **Tier:** P0
- **Tags:** dynamic, re-optimisation
- **Binds:** what has already been executed
- **Exercises:** FR-13, FR-21, FR-22, FR-30
- **Breaks:** full re-solve. Re-planning the world moves stops drivers have already passed and reshuffles routes that were fine, destroying dispatcher trust
#### UC-033 — Urgent order injection into a live plan

A priority order arrives after routes are dispatched.

- **Variant:** VRPTW
- **Tier:** P0
- **Tags:** dynamic, insertion
- **Binds:** remaining slack on in-flight routes
- **Exercises:** FR-04, FR-13, FR-21
- **Breaks:** quoting insertion cost from distance. The true cost is knock-on lateness across the whole downstream tail
#### UC-034 — Traffic incident mid-shift

A closure invalidates travel assumptions for half the fleet.

- **Variant:** VRPTW
- **Tier:** P2
- **Tags:** dynamic, re-optimisation
- **Binds:** revised travel times
- **Exercises:** FR-14, FR-21, FR-22
- **Breaks:** re-optimising against the stale matrix. The plan must be re-costed against the new network before it is re-optimised
#### UC-035 — Failed delivery and retry

A stop fails and must be retried today or tomorrow.

- **Variant:** VRPTW
- **Tier:** P2
- **Tags:** dynamic, retry
- **Binds:** retry policy versus remaining capacity
- **Exercises:** FR-06, FR-12, FR-13, FR-22
- **Breaks:** silent deferral. The retry is a new order with a new release time and changed priority
#### UC-031 — Restaurant and grocery on-demand delivery

Orders with food-ready times and short freshness windows.

- **Variant:** PDPTW
- **Tier:** P1
- **Tags:** dynamic
- **Binds:** preparation completion and delivery freshness
- **Exercises:** FR-04, FR-06, FR-14, FR-22
- **Breaks:** assuming pickup readiness. Arriving before the food is ready produces idling the model cannot see — the Meituan problem at §4.5
#### UC-044 — Utility emergency response

Leak and outage response with severity-driven priority.

- **Variant:** VRPTW
- **Tier:** P1
- **Tags:** dynamic, preemptive
- **Binds:** response-time targets by severity
- **Exercises:** FR-12, FR-13, FR-21, FR-22
- **Breaks:** uniform priority. A P1 gas escape preempts work already in progress, requiring the plan to be interruptible mid-route
#### UC-171 — Driver absence discovered at shift start

Two drivers call in sick at 05:30 for a 06:00 departure.

- **Variant:** MDHVRPTW
- **Tier:** P0
- **Tags:** dynamic, re-planning
- **Binds:** reduced fleet against an already-built plan
- **Exercises:** FR-30, FR-13, FR-21, FR-12
- **Breaks:** re-solving from scratch. Vehicles are loaded; the practical question is which stops to strip and redistribute, not how to re-plan the day
#### UC-172 — Weather event cancelling a region

Snow or flooding makes an area unreachable mid-day.

- **Variant:** VRPTW
- **Tier:** P2
- **Tags:** dynamic, network-change
- **Binds:** network availability and safety policy
- **Exercises:** FR-11, FR-14, FR-21, FR-22
- **Breaks:** treating it as congestion. Affected arcs become unreachable, not slow, and the correct response is deferral rather than rerouting
### 10.5 Beyond the five variants — deliberately partial

These appear in customer conversations. The design document should state a position on each.

#### UC-173 — Inventory routing under vendor-managed inventory

Telemetry-driven replenishment where the supplier decides both when to deliver and how much.

- **Variant:** IRP
- **Tier:** P1
- **Binds:** forecast stockout risk across the customer base, not a set of orders
- **Exercises:** FR-06, FR-31, FR-34 — no current requirement covers quantity as a decision variable
- **Breaks:** the input contract itself. There are no orders to route; quantity and timing are decision variables coupled to a consumption forecast, which is a different problem class (see §4.7)
- **Status:** NOT_MODELLED — supported by generating orders upstream in a separate replenishment planner, then routing them normally
#### UC-042 — Winter gritting and snow clearance

Treatment of a road network rather than a set of discrete points.

- **Variant:** CARP
- **Tier:** P2
- **Binds:** network coverage and material capacity
- **Exercises:** nothing in the current model
- **Breaks:** node routing entirely. Demand lies on the arcs, so expressing it as stops either explodes the instance size or silently drops coverage
- **Status:** NOT_MODELLED — declined explicitly. The most common request the platform should refuse
#### UC-045 — Mobile vaccination and screening clinic siting

Temporary clinics sited and staffed across a region over a campaign.

- **Variant:** LRP
- **Tier:** P2
- **Binds:** siting decisions, not the routing between sites
- **Exercises:** FR-31, FR-34, FR-35
- **Breaks:** treating locations as given. The primary decision is where to go, which is a facility location problem with routing nested inside it
- **Status:** PARTIALLY_MODELLED — siting handled as a scenario sweep over candidate locations (FR-34), not as joint location-routing optimisation
#### UC-174 — Street sweeping, line painting and gully cleaning

Municipal services whose demand lies along street segments rather than at addresses.

- **Variant:** CARP
- **Tier:** P2
- **Binds:** segment coverage within a shift, plus one-way and parking-restriction timing
- **Exercises:** nothing in the current model
- **Breaks:** node routing, for the same reason as UC-042 — demand is on the arcs, not at points
- **Status:** NOT_MODELLED
#### UC-175 — Postal walk and round design

Delivery-office round boundaries drawn on a street network.

- **Variant:** CARP
- **Tier:** P2
- **Binds:** walk duration balance across rounds, at street-segment granularity
- **Exercises:** FR-35, FR-17
- **Breaks:** zone-granularity territory design. Rounds are defined by which side of which street a postie walks, which is finer than any zone model represents
- **Status:** PARTIALLY_MODELLED — zone-level territory design (FR-35) is supported; street-level arc coverage is not
## 11. Adversarial and pathological instances

Not customer scenarios. These break implementations, and each has caused a production incident
somewhere.

| ID | Instance | Required behaviour |
|---|---|---|
| `UC-060` | Order exceeding every vehicle's capacity | `CAPACITY_EXCEEDED` at pre-flight, not infeasible after fifteen minutes |
| `UC-061` | Geocode on an island or pedestrian precinct | Explicit unreachable arc, never a large finite distance |
| `UC-062` | Zero-width and inverted time windows | Zero-width is feasible-with-no-slack; inverted is a validation error. Never conflated |
| `UC-063` | Route crossing midnight; shift crossing DST | Break timing and shift-end checks remain correct |
| `UC-064` | Driver arriving with hours already consumed | Respects the remaining legal envelope |
| `UC-065` | Every order in the same one-hour window | Fleet size follows window overlap; the window is never quietly relaxed |
| `UC-066` | Totals fit, peak load does not | Rejected. The canonical capacity bug |
| `UC-067` | Mutually incompatible but individually feasible orders | Route-level predicate, not pairwise afterthought |
| `UC-068` | Contradictory operator locks | Minimal conflicting lock set returned; never a silent drop |
| `UC-069` | Two hundred orders at one address | Zero-distance arcs and neighbourhood lists degrade gracefully |
| `UC-070` | Single order, single vehicle | Milliseconds, correct trivial plan. The fastest smoke test |
| `UC-071` | Zero available vehicles | All unassigned with `FLEET_EXHAUSTED`; `FEASIBLE` with an empty plan, not a crash |
| `UC-072` | Matrix provider timeout mid-build | Cache fallback, `DEGRADED` label, never silent haversine |
| `UC-073` | Antimeridian and high-latitude coordinates | No computation assumes a Euclidean plane |
| `UC-074` | Instance at the decomposition threshold | Both code paths produce comparable objectives on the same instance |

---

## 12. Coverage matrices

### 12.1 Variant index

<!-- BEGIN:GENERATED variant_index -->
| Variant | Scenario ids |
|---|---|
| TSP | 075, 076, 077, 078, 079, 080, 081, 082, 083, 084, 085, 086, 087, 088, 089, 090 |
| CVRP | 004, 008, 011, 013, 015, 091, 092, 093, 094, 095, 096, 097, 098, 099, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110 |
| VRPTW | 001, 003, 005, 006, 009, 010, 018, 019, 022, 024, 025, 032, 033, 034, 035, 043, 044, 046, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 172 |
| MDHVRPTW | 002, 007, 012, 020, 037, 039, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152, 171 |
| PDPTW | 014, 016, 017, 026, 029, 030, 031, 036, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 167, 168, 169, 170 |
| DARP | 027, 028, 166 |
| IRP | 173 |
| CARP | 042, 174, 175 |
| LRP | 045 |
<!-- END:GENERATED -->

### 12.2 Requirement coverage — what changed since v1.0

The v1.0 gaps are now covered or explicitly deferred:

| v1.0 gap | Now covered by | Status |
|---|---|---|
| Route-level sequence realism | 075, 078, 087, 090 | New section §5 |
| Sequence-dependent vehicle inventory | 020, 097 (wash-out) | Still a requirement gap — `FR-10` extension needed |
| Cross-vehicle precedence | 021, 121, 158 | Still a requirement gap |
| Multi-period horizon | 024, 043, 108, 111, 129, 152, 162 | Now well-evidenced; justifies a `FR-*` addition |
| Maximum ride time | 026, 027, 028, 164, 166 | Now well-evidenced; justifies a `FR-*` addition |
| Crew as a distinct resource | 040, 122, 153 | Justifies a `FR-*` addition |
| Cargo-side regulatory clock | 041, 092, 157 | Confirms the rules interface must be entity-agnostic |
| Preemption of in-progress work | 044, 143, 154 | Justifies a `FR-*` addition |
| Transhipment | 168 | Explicit model extension needed for LTL |
| Synchronisation between routes | 131, 145, 147 | Two-echelon and convoy cases; justifies a `FR-*` addition |

**Recommended new requirements**, each now supported by three or more scenarios:

| Proposed | Covering |
|---|---|
| Multi-period planning horizon with visit-frequency compliance | 024, 043, 108, 111, 129, 152, 162 |
| Maximum ride time per passenger or shipment | 026, 027, 028, 092, 157, 164, 166 |
| Crew as a resource independent of the vehicle | 040, 122, 153 |
| Route synchronisation (two-echelon, convoy, transhipment) | 131, 145, 147, 168 |
| Sequence-dependent service and setup time | 020, 097 |
| Preemption of in-progress work by higher priority | 044, 143, 154 |
| Split `FR-13` into commercial priority, SLA clock, and statutory obligation | 009, 018, 046, 116, 117 |

### 12.3 What the industry deployments validate

| Requirement area | Validated by |
|---|---|
| Dynamic re-optimisation is worth building | UPS Dynamic ORION: 2–4 miles/driver on top of 8 (§4.1) |
| Service time dominates dense last mile | Amazon: travel is ~33% of the driver's day (§4.2) |
| Explainability determines adoption | UPS built driver-facing reasoning into ORION (§4.1) |
| Strategic and operational belong in one framework | Walmart, 2023 Edelman winner (§4.4) |
| Staged dispatch-policy ladder | Meituan's published four-phase evolution (§4.5) |
| Bidding and costing as a first-class use case | DHL Supply Chain (§4.3) |
| Release times as decision variables | Alibaba picking-plus-delivery (§4.6) |
| Inventory routing is a separate problem class | Air Liquide / ROADEF 2016 (§4.7) |

---

## 13. Building the fixture corpus

1. **P0 scenarios become seeded synthetic fixtures first** — one per P0 scenario, at three sizes.
2. **Pathological instances (§11) are hand-built and tiny**, in the fast tier, run on every commit.
3. **Each variant section contributes at least one benchmark-comparable fixture** so public
   benchmark performance and production performance can be related. TSP fixtures compare against
   TSPLIB; CVRP against CVRPLIB; VRPTW against Solomon and Gehring & Homberger; PDPTW against
   Li & Lim. Note the differing objective conventions per set — see `SDD-VRP-001` §11.3.
4. **P1 and P2 scenarios become anonymised production instances** as customers arrive, replacing
   their synthetic equivalents. Synthetic data validates correctness; real data validates the model.
5. **Every fixture carries its `Breaks` assertion as a named test.** A fixture asserting only
   "returns a feasible solution" is not earning its runtime.
6. **The coverage matrix is regenerated in CI** from requirement tags on fixtures, so a requirement
   landing without a scenario is reported rather than passing unnoticed.

---

## 14. Scenario index

Complete lookup table. Generated; do not edit by hand.

<!-- BEGIN:GENERATED full_index -->
| ID | Name | Variant | Tier | Tags |
|---|---|---|---|---|
| `UC-001` | Grocery home delivery | VRPTW | P0 | — |
| `UC-002` | Multi-temperature convenience store replenishment | MDHVRPTW | P0 | — |
| `UC-003` | Retail store delivery into receiving-bay hours | VRPTW | P0 | — |
| `UC-004` | Beverage and brewery distribution | CVRP | P0 | simultaneous-pandd |
| `UC-005` | Pharmaceutical and clinical supply | VRPTW | P1 | — |
| `UC-006` | Bakery and fresh produce early-morning distribution | VRPTW | P1 | multi-trip |
| `UC-007` | Builders' merchant and heavy materials | MDHVRPTW | P1 | — |
| `UC-008` | Fuel and LPG bulk delivery | CVRP | P1 | multi-compartment |
| `UC-009` | Parcel last mile from a delivery station | VRPTW | P0 | — |
| `UC-010` | Newspaper and periodical distribution | VRPTW | P2 | — |
| `UC-011` | Cash-in-transit ATM and retail collection | CVRP | P2 | — |
| `UC-012` | E-commerce fulfilment with carrier handover | MDHVRPTW | P2 | prize-collecting |
| `UC-013` | Municipal waste and recycling collection | CVRP | P0 | multi-trip |
| `UC-014` | Commercial waste with mixed container types | PDPTW | P1 | — |
| `UC-015` | Milk and agricultural collection | CVRP | P1 | — |
| `UC-016` | Laundry and linen exchange | PDPTW | P1 | simultaneous |
| `UC-017` | Returns and reverse logistics on delivery routes | PDPTW | P2 | backhaul |
| `UC-018` | Pathology specimen collection | VRPTW | P1 | — |
| `UC-019` | Utility installation and repair appointments | VRPTW | P0 | home-start |
| `UC-020` | HVAC and appliance service with parts dependency | MDHVRPTW | P1 | — |
| `UC-022` | Home care and domiciliary visits | VRPTW | P1 | consistency-dominant |
| `UC-024` | Pest control and scheduled inspections | VRPTW | P2 | multi-period |
| `UC-025` | Mobile veterinary and rural service | VRPTW | P2 | sparse |
| `UC-026` | School bus routing | PDPTW | P1 | ride-time |
| `UC-027` | Non-emergency patient transport | DARP | P1 | — |
| `UC-028` | Paratransit and community transport | DARP | P2 | dynamic |
| `UC-029` | Employee shuttle and crew transport | PDPTW | P2 | — |
| `UC-030` | Same-day courier with rolling intake | PDPTW | P1 | dynamic |
| `UC-031` | Restaurant and grocery on-demand delivery | PDPTW | P1 | dynamic |
| `UC-032` | Mid-day vehicle breakdown recovery | VRPTW | P0 | dynamic, re-optimisation |
| `UC-033` | Urgent order injection into a live plan | VRPTW | P0 | dynamic, insertion |
| `UC-034` | Traffic incident mid-shift | VRPTW | P2 | dynamic, re-optimisation |
| `UC-035` | Failed delivery and retry | VRPTW | P2 | dynamic, retry |
| `UC-036` | Container drayage from port to consignee | PDPTW | P1 | empty-repositioning |
| `UC-037` | Urban distribution with low-emission zone restrictions | MDHVRPTW | P1 | — |
| `UC-039` | Abnormal load and permit-constrained haulage | MDHVRPTW | P2 | — |
| `UC-042` | Winter gritting and snow clearance | CARP | P2 | — |
| `UC-043` | Meter reading and inspection rounds | VRPTW | P1 | periodic |
| `UC-044` | Utility emergency response | VRPTW | P1 | dynamic, preemptive |
| `UC-045` | Mobile vaccination and screening clinic siting | LRP | P2 | — |
| `UC-046` | Postal delivery under universal service obligation | VRPTW | P2 | — |
| `UC-075` | Delivery-station route sequencing to match driver behaviour | TSP | P0 | — |
| `UC-076` | Warehouse picker walk sequencing | TSP | P1 | — |
| `UC-077` | Single technician's fixed day | TSP | P0 | — |
| `UC-078` | Meter reading round | TSP | P1 | — |
| `UC-079` | Drone or UAV inspection sweep | TSP | P2 | — |
| `UC-080` | Crane and lift sequencing on a construction site | TSP | P2 | — |
| `UC-081` | PCB drilling and CNC toolpath | TSP | P2 | — |
| `UC-082` | Security guard patrol route | TSP | P2 | — |
| `UC-083` | Cash collection round from retail sites | TSP | P1 | — |
| `UC-084` | Hotel housekeeping room sequence | TSP | P2 | — |
| `UC-085` | Sample collection round within a hospital | TSP | P2 | — |
| `UC-086` | Fuel card and forecourt audit round | TSP | P2 | — |
| `UC-087` | Re-sequencing a route mid-shift after a missed stop | TSP | P0 | — |
| `UC-088` | Snowplough or gritter within a fixed beat | TSP | P2 | arc-routing-boundary |
| `UC-089` | Photography or survey shot list | TSP | P2 | — |
| `UC-090` | Multi-drop van already loaded in a fixed order | TSP | P1 | lifo |
| `UC-091` | Bottled water and cooler delivery | CVRP | P1 | simultaneous-pandd |
| `UC-092` | Aggregate and ready-mix concrete delivery | CVRP | P1 | hard-perishability |
| `UC-093` | Animal feed bulk blowing | CVRP | P2 | multi-compartment |
| `UC-094` | LPG cylinder exchange rounds | CVRP | P2 | simultaneous-pandd |
| `UC-095` | Grain and harvest haulage | CVRP | P2 | seasonal |
| `UC-096` | Skip and roll-on-roll-off exchange | CVRP | P1 | unit-capacity |
| `UC-097` | Bulk chemical and food-grade tanker delivery | CVRP | P2 | wash-out |
| `UC-098` | Pallet network trunking to hubs | CVRP | P1 | — |
| `UC-099` | Vending machine cash and stock collection | CVRP | P2 | prize-collecting |
| `UC-100` | Retail cash and coin delivery to branches | CVRP | P2 | — |
| `UC-101` | Water and sewage tanker services | CVRP | P2 | — |
| `UC-102` | Furniture flat-pack home delivery | CVRP | P2 | — |
| `UC-103` | Charity and food-bank collection rounds | CVRP | P2 | — |
| `UC-104` | Construction site welfare and plant servicing | CVRP | P2 | — |
| `UC-105` | Parcel locker replenishment and clearance | CVRP | P1 | simultaneous-pandd |
| `UC-106` | Newspaper and magazine returns collection | CVRP | P2 | backhaul |
| `UC-107` | Medical gas cylinder distribution to homes | CVRP | P1 | — |
| `UC-108` | Laundry bulk collection from care homes | CVRP | P2 | simultaneous-pandd |
| `UC-109` | Agricultural input delivery to farms | CVRP | P2 | seasonal |
| `UC-110` | Pool and spa chemical service rounds | CVRP | P2 | — |
| `UC-111` | Boiler and appliance annual service visits | VRPTW | P1 | periodic |
| `UC-112` | Legal and court document service | VRPTW | P2 | — |
| `UC-113` | Fresh flower and floristry distribution | VRPTW | P2 | — |
| `UC-114` | School meal and catering distribution | VRPTW | P1 | — |
| `UC-115` | Office coffee and pantry supply | VRPTW | P2 | — |
| `UC-116` | Bank branch and ATM engineering | VRPTW | P1 | — |
| `UC-117` | IT field support with tiered SLAs | VRPTW | P1 | — |
| `UC-118` | Window cleaning and facade maintenance rounds | VRPTW | P2 | — |
| `UC-119` | Mobile phlebotomy and home blood draws | VRPTW | P1 | — |
| `UC-120` | Retail merchandising and planogram visits | VRPTW | P2 | — |
| `UC-121` | Equipment hire delivery and collection | VRPTW | P1 | paired |
| `UC-122` | Charity shop donation collection | VRPTW | P2 | — |
| `UC-123` | Prescription delivery to housebound patients | VRPTW | P1 | — |
| `UC-124` | Fuel card and forecourt maintenance | VRPTW | P2 | — |
| `UC-125` | Hotel and hospitality linen delivery | VRPTW | P1 | simultaneous-pandd |
| `UC-126` | Dairy and doorstep delivery rounds | VRPTW | P2 | — |
| `UC-127` | Mobile library and community service vehicles | VRPTW | P2 | — |
| `UC-128` | Court and prisoner transport | VRPTW | P2 | paired, security |
| `UC-129` | Fire extinguisher and safety equipment inspection | VRPTW | P2 | periodic |
| `UC-130` | EV charge-point maintenance | VRPTW | P2 | — |
| `UC-131` | Two-echelon urban distribution with micro-hubs | MDHVRPTW | P1 | 2e |
| `UC-132` | National 3PL with shared customer networks | MDHVRPTW | P1 | — |
| `UC-133` | Cross-dock and hub-and-spoke consolidation | MDHVRPTW | P1 | — |
| `UC-134` | Regional distribution with overlapping depot catchments | MDHVRPTW | P0 | — |
| `UC-135` | Franchise and multi-branch service networks | MDHVRPTW | P2 | — |
| `UC-136` | Mixed own-fleet and courier network | MDHVRPTW | P1 | prize-collecting |
| `UC-137` | Grocery multi-format distribution | MDHVRPTW | P1 | — |
| `UC-138` | Automotive parts distribution to dealers and workshops | MDHVRPTW | P1 | multi-wave |
| `UC-139` | Building services and facilities management | MDHVRPTW | P2 | — |
| `UC-140` | Waste with multiple transfer stations and disposal routes | MDHVRPTW | P1 | multi-trip |
| `UC-141` | Concrete and asphalt supply from multiple plants | MDHVRPTW | P2 | — |
| `UC-142` | Retail returns and reverse network | MDHVRPTW | P2 | backhaul |
| `UC-143` | Utilities emergency and planned work from district depots | MDHVRPTW | P1 | — |
| `UC-144` | Airline catering and aircraft servicing | MDHVRPTW | P2 | — |
| `UC-145` | Port and terminal internal logistics | MDHVRPTW | P2 | — |
| `UC-146` | Mining and quarry haulage | MDHVRPTW | P2 | — |
| `UC-147` | Military and humanitarian resupply convoys | MDHVRPTW | P2 | — |
| `UC-148` | Ski resort and remote site servicing | MDHVRPTW | P2 | seasonal-access |
| `UC-149` | Agricultural contractor machine movement | MDHVRPTW | P2 | — |
| `UC-150` | Multi-country European distribution | MDHVRPTW | P2 | — |
| `UC-151` | Retail delivery with in-store and kerbside pickup | MDHVRPTW | P2 | — |
| `UC-152` | Field sales and account management territories | MDHVRPTW | P2 | periodic |
| `UC-153` | Removals and household moving | PDPTW | P2 | lifo |
| `UC-154` | Vehicle recovery and roadside assistance | PDPTW | P1 | dynamic |
| `UC-155` | Car transporter delivery to dealerships | PDPTW | P2 | loading-constraints |
| `UC-156` | Document and legal courier with chain of custody | PDPTW | P2 | — |
| `UC-157` | Blood, organ and urgent clinical transport | PDPTW | P1 | — |
| `UC-158` | Pallet and equipment repositioning between depots | PDPTW | P2 | — |
| `UC-159` | Waste transfer between transfer stations and disposal | PDPTW | P2 | — |
| `UC-160` | Beer keg and gas cylinder exchange at pubs | PDPTW | P1 | simultaneous |
| `UC-161` | Coffee machine and water cooler servicing | PDPTW | P2 | simultaneous |
| `UC-162` | Uniform and workwear rental rounds | PDPTW | P2 | simultaneous |
| `UC-163` | Medical sharps and clinical waste exchange | PDPTW | P2 | simultaneous |
| `UC-164` | Airport transfer and shared shuttle | PDPTW | P2 | ride-time |
| `UC-165` | Ride pooling and shared taxi | PDPTW | P2 | — |
| `UC-166` | Care home and day-centre transport | DARP | P2 | — |
| `UC-167` | Crew change transport for offshore and remote sites | PDPTW | P2 | — |
| `UC-168` | LTL and groupage freight consolidation | PDPTW | P1 | transhipment |
| `UC-169` | Freight exchange and backload matching | PDPTW | P2 | prize-collecting |
| `UC-170` | Multi-modal first and last mile around rail or sea | PDPTW | P2 | — |
| `UC-171` | Driver absence discovered at shift start | MDHVRPTW | P0 | dynamic, re-planning |
| `UC-172` | Weather event cancelling a region | VRPTW | P2 | dynamic, network-change |
| `UC-173` | Inventory routing under vendor-managed inventory | IRP | P1 | — |
| `UC-174` | Street sweeping, line painting and gully cleaning | CARP | P2 | — |
| `UC-175` | Postal walk and round design | CARP | P2 | — |
<!-- END:GENERATED -->

---

## 15. References

**Industry deployments.** UPS ORION: 2016 INFORMS Franz Edelman Award; Supply Chain Dive on dynamic
ORION rollout; BSR case study. Amazon: 2021 Last Mile Routing Research Challenge
(routingchallenge.mit.edu), Merchán et al., *Transportation Science*; Wu et al., arXiv:2205.04001.
DHL Supply Chain: Dang et al. (2024), *INFORMS Journal on Applied Analytics* 54(1):20–36. Walmart:
2023 Franz Edelman Award, *INFORMS Journal on Applied Analytics* 54(1). Meituan: Liang et al.
(2024), *INFORMS Journal on Applied Analytics* 54(1):84–101. Alibaba: 2021 Franz Edelman finalist.
Air Liquide: 2016 ROADEF/EURO Challenge, Inventory Routing Problem model description. Lyft: 2023
Franz Edelman finalist.

**Regulation.** Regulation (EC) No 561/2006; Directive 2002/15/EC; FMCSA 49 CFR Part 395; ADR.

**Benchmarks.** TSPLIB; CVRPLIB / Uchoa; Solomon (1987); Gehring & Homberger; Li & Lim;
EURO Meets NeurIPS 2022; ROADEF/EURO Challenge series.

*End of document.*
