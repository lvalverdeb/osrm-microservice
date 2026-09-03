# Feasibility Is a Gate; Optimality Is a Target

**An advanced technical report** on the vehicle-routing platform in
[`vrp/`](../../vrp) — 15,977 lines of Python across 59 modules (measured 2026-09-02), built to
[`docs/vrp-spec-driven-development.md`](../vrp-spec-driven-development.md).

For engineers extending the platform and for anyone evaluating its
architecture. Assumes [Paper 02](02-what-the-gateway-costs.md) and some
familiarity with combinatorial optimisation.

This report contains three measurements the repository did not previously have:
what the gateway's routing costs against a real solver, what that quality costs
in compute, and the independent verifier catching seeded defects on a real plan.

---

## 1. The thesis

Most writing about vehicle routing is about search. This platform is organised
around a different claim, its first constitutional principle:

> **A plan that violates a hard constraint is worthless no matter how cheap it
> is.** The system MUST never emit a route plan claimed to be feasible without
> it passing an independent feasibility checker that does not share code with
> the solver. Cost is a target; feasibility is a gate.

Everything structural follows: the solver is replaceable behind a stable
interface, the verifier is a separate package with no shared code, the evaluator
is written to be obviously right rather than fast, the objective is a
lexicographic hierarchy rather than a weighted sum. Each is a *cost* if the hard
problem is search, and *load-bearing* if the hard problem is being sure.

Its empirical companion:

> **CON-2 — The solver is the small part.** The overwhelming majority of
> real-world routing failures are data failures: wrong geocodes, stale
> travel-time matrices, mis-specified service times, capacity units that
> disagree between systems, and orders that were never eligible for dispatch.

Paper 01 §5 puts a number on that: reaching 50,000 usable Costa Rica addresses
meant discarding 49% of candidates, half of them unreachable by road. The data
layer rejected more addresses than the solver will ever route.

---

## 2. Layers

```
A. Intake & Validation    schema, units, referential integrity, geocoding,
                          snapping, pre-flight infeasibility diagnosis
B. Geospatial & Matrix    per-profile matrices, time buckets, sparsification,
                          content-addressed and cached
C. Model Compiler         domain Problem → solver-native model
D. Solver Portfolio       PyVRP | OR-Tools | custom LNS, plus decomposition
E. Verification           independent feasibility checker, INV-1…INV-16
F. Explanation            per-order rationale, marginal costs
G. Dispatch & Execution   wave controller, locks, telematics
H. Learning & Calibration service times, speeds, plan adherence
```

**A, B, E, F and H are where the durable value lives. D is replaceable.** That
sentence is the architecture.

---

## 3. Experiment: what the gateway's routing costs

The repository ships two routing paths and has never compared them, because
nothing connects them. The gateway's `/vrp` is sweep-angle allocation plus one
OSRM `/trip` per chunk; `vrp/` is a solver portfolio behind a canonical
objective. No shared scale, so "which is better" had no answer.

`experiments/e02_heuristic_vs_solver.py` puts both on one scale: pin a single
matrix from the live gateway, take the gateway's own plan and a PyVRP solution
for the identical instance and identical fleet, and score **both** with
`vrp.evaluator` — exactly as `vrp.portfolio` does for competing engines, because
an engine's own accounting is never evidence about that engine.

60 GAM stops, one depot, PyVRP at 2,000 iterations, seed 0
(`results/e02_heuristic_vs_solver.json`):

| Fleet | Gateway plan | Solver plan | Gap | Agreement delta |
|---|---:|---:|---:|---:|
| 1 vehicle (cap 60) | 341,105 m | 315,173 m | **+8.2%** | −0.1 m |
| 3 vehicles (cap 20) | 391,217 m | 350,332 m | **+11.7%** | −0.5 m |
| 6 vehicles (cap 10) | 481,715 m | 419,593 m | **+14.8%** | −0.9 m |

All six plans pass the independent verifier. All 60 orders are assigned in
every case, and both sides deploy the same number of vehicles.

Three readings.

**The single-vehicle gap isolates sequencing.** With one vehicle there is no
partition to get wrong, so +8.2% is what OSRM `/trip` gives up against a
dedicated solver on pure ordering. That is a respectable showing for a
general-purpose TSP service.

**The gap grows with the fleet — 8.2% → 11.7% → 14.8%.** This is the sweep
paying for itself. `/vrp` cuts each vehicle's load by compass angle *before*
anything is optimised, and no later step revisits the partition. The more
vehicles, the more partition decisions are frozen, and the more it costs. This
is the structural limit of the gateway's routing, and it is now quantified.

**The agreement delta is the quiet result.** The gateway reports
`total_distance`; recomputing it from the gateway's own sequences on the pinned
matrix agrees to within 0.9 m on plans of 341–482 km — pure decimal rounding.
That is INV-9 holding **across a language boundary**, between an independent
Rust implementation and the Python canonical evaluator. Nothing in CI checks
this today. It is the strongest available evidence that the two implementations
mean the same thing by "a plan's distance", and it would be worth a test.

---

## 4. Experiment: what that quality costs in compute

The other half of the trade. `experiments/e03_anytime.py`, same instance,
3 vehicles, ladder of budgets (`results/e03_anytime.json`):

| Budget | Distance | Search time | vs gateway |
|---:|---:|---:|---:|
| gateway | 391,217 m | **11.9 ms** end-to-end | — |
| 25 iters | 363,537 m | 12.1 ms | **−7.1%** |
| 50 | 359,930 m | 15.7 ms | −8.0% |
| 100 | 355,224 m | 24.8 ms | −9.2% |
| 200 | 351,703 m | 41.6 ms | −10.1% |
| 400–1,600 | 351,703 m | 81–283 ms | −10.1% |
| 3,200 | **350,332 m** | 560.9 ms | **−10.5%** |
| 6,400 | 350,332 m | 1,127.6 ms | −10.5% |

**At comparable wall-clock the solver is already 7% better.** The gateway takes
11.9 ms end to end; 12.1 ms of search beats it by 7.1%. Nearly all the available
gain — 10.1 of 10.5 points — arrives by 200 iterations and 42 ms. Everything
after that is 3.6% of refinement over a 27× budget increase, and the curve is
flat from 200 to 1,600.

**The comparison is not free of caveats and they run in the gateway's favour.**
The 11.9 ms is genuinely end to end, including the gateway's matrix fetch and
its `/trip` round trips over the network. The solver's milliseconds are search
only, on an already-pinned matrix; add ~36 ms for a 50×50 matrix
([Paper 02 §2](02-what-the-gateway-costs.md)) for a fully-loaded figure. Even
so, the conclusion survives: **the quality the sweep gives up is not buying
meaningful time.** It is available for roughly the same money.

This also demonstrates NFR-03's anytime requirement, which nothing in the
repository previously plotted: a usable incumbent at 12 ms, improving
monotonically, converged by 3,200 iterations.

---

## 5. Experiment: does the verifier actually catch things?

CON-1 makes feasibility a gate and `T-04`'s definition of done claims the
verifier "detects seeded violations in 100% of mutation tests". Asserted in the
backlog, exercised in the suite, shown nowhere.

`experiments/e06_mutation.py` builds a verified plan from real road distances,
breaks it six ways a solver plausibly could, and records what the verifier said
(`results/e06_mutation.json`). The clean plan passes.

| Mutation | Expected | Fired | Detail returned |
|---|---|---|---|
| Order dropped from a route | INV-1 | INV-1, INV-4, INV-9 | `neither served nor listed unassigned` |
| Order placed on two routes | INV-1 | INV-1, INV-4, INV-9 | `served 2 times, expected 1` |
| Arrival arithmetic broken | INV-4 | INV-3, INV-4 | `service starts 461 before arrival 1361` |
| Service before arrival | INV-3 | INV-3 | `service starts -139 before arrival 461` |
| Capacity exceeded | INV-5 | INV-5 | `load stops=25 exceeds capacity 20` |
| Objective misreported | INV-9 | INV-9 | `reported distance=325332, recomputed 350332` |

**Six of six caught, each naming the invariant it should.** The second column is
the one that matters: a checker that fails for the wrong reason sends somebody
to the wrong module.

Two observations. The violations **name the order and the vehicle**, so the
report is actionable rather than a boolean — CON-5's explainability requirement
reaching down into the verifier. And the invariants are **not independent**:
dropping one order trips INV-1, INV-4 and INV-9 together, because removing a
step breaks coverage, the arrival chain, and the objective at once. A single
defect surfacing three times is a feature — it means several independent
recomputations would each have caught it alone.

---

## 6. The constitution

Eleven principles bind every downstream decision; an implementation violating
one is rejected at review regardless of measured performance. The five that most
shape the code:

| | Principle | Consequence |
|---|---|---|
| CON-1 | Feasibility is a gate | §5's verifier, separately packaged and authored |
| CON-2 | The solver is the small part | A, B, E, F, H carry the value; D is replaceable |
| CON-3 | Model the business, then choose the algorithm | No solver concept may leak into the domain layer |
| CON-4 | Determinism and reproducibility | Integer arithmetic in fixed units; float accumulation in objectives prohibited; budgets in deterministic units |
| CON-11 | A constraint the search cannot carry is refused, never assumed | §11 |

Three more, unusual to see written down: **CON-5**, explainability is a product
requirement, because dispatchers silently override plans they cannot explain;
**CON-6**, plan quality is measured against executed GPS traces rather than the
solver's own objective, and systematic driver deviation is a *model defect*;
**CON-7**, operator locks are hard constraints in the next re-optimisation, never
silently discarded.

CON-11 carries an explicit **PROPOSED — not yet in force** banner: it describes
practice the code already follows, but until sign-off is in the amendment log,
nothing may cite it as review authority. A governing document that distinguishes
what binds from what is merely written is one you can actually govern with.

---

## 7. The domain model and sixteen invariants

`vrp/model.py` states the problem independently of any solver, in **integers in
fixed units** — metres, seconds, cost-cents — with float accumulation in
objectives prohibited outright. CON-4 requires byte-identical output for
identical inputs; float accumulation makes that unachievable in principle,
because summation order changes with parallelism. You cannot replay a run you
cannot reproduce, or debug a plan you cannot replay.

Sixteen invariants are checked by the verifier. The first nine were specified up
front — coverage, pickup-before-delivery, timeline arithmetic against the
**pinned matrix version**, capacity, route limits, hours of service, locks, and
INV-9's objective recomputation. Seven more were added as requirements arrived:
skills and access classes, reload rules, depot bay capacity, **global** depot
inventory, ride time, synchronisation, and INV-16's electric range.

§4.3 is explicit about why they are listed in the specification and not only in
the verifier's source:

> Each covers a plan that satisfied every invariant this section named while
> being one nobody could drive. They are listed here rather than only in
> `vrp/verify/verifier.py` because a specification that understates what the
> system checks invites somebody to rely on a guarantee it does not know it
> makes.

**INV-9 is called the single most valuable test in the system**, and §3's
agreement delta is why. Most silent optimisation bugs are objective drift
between the fast incremental evaluator inside local search and ground truth: the
search believes it is improving, the plan gets worse, nothing errors.

---

## 8. Evaluator, verifier, and the public `/verify`

`vrp/evaluator.py` recomputes a route's timeline and objective from first
principles — sequence, pinned matrix, nothing else. No incremental state, no
caching. Its docstring states the rule: *written to be obviously right rather
than fast.* This is the correct trade for a ground truth and an unusual
discipline to hold, because the moment it becomes clever it stops being evidence
about the clever evaluator it exists to check.

`vrp/verify/verifier.py` is a separate module in a separate package with no
shared code with any solver. The specification requires it be **written by a
different author than the solver adapter** and **not import the evaluator used
inside local search**; discrepancies are P1 defects.

It is reachable through a **public** `/verify` contract (`vrp/api.py`) accepting
externally supplied plans. Two reasons, the second being the interesting one:
integrators can check plans produced elsewhere; and it forces the verifier to be
genuinely independent, because a verifier that can only check its own solver's
plans shares that solver's assumptions — and those assumptions are where the bug
hides.

Hence a strict parser. An external plan arrives from a system with its own
types, rounding and optionality. The helpful move — infer a missing arrival,
coerce `"600"` to `600`, default an absent window — produces a report about a
plan the integrator did not send, and it would *pass*, which is worse than
failing. So the parser refuses and names the field.

---

## 9. The objective is a hierarchy, not a sum

Weighted sums are named the most common modelling error in production routing:
weights that balance on a 200-stop day silently invert on a 2,000-stop day.
Instead, a lexicographic hierarchy — hard violations, unserved priority-0,
unserved by priority, fleet cost, operating cost, soft violations, tie-breakers
— realised by scaling against each tier's maximum *attainable* value computed
from the instance, or by staged optimisation (preferred above 10,000 stops,
where scaled weights risk integer overflow).

**A mode does not reorder the hierarchy; it changes which tiers share a level.**
Two rows repay reading twice, and an implementation treating every mode as
strictly lexicographic passes most tests and is wrong in both:

- **`MIN_COST` collapses into `MIN_VEHICLES`** if fleet cost strictly dominates
  operating cost, because one fewer vehicle always wins. The intent is a
  *trade*: deploy a vehicle exactly when its fixed cost is repaid.
- **`PRIZE_COLLECTING` can never drop anything** if unserved-order cost
  dominates. It is the only mode where it does not — total prize is a constant,
  so the mode minimises forgone prize plus cost in one currency. Priority-0 stays
  above: a promise, not a bid.

---

## 10. The matrix subsystem fails more often than the solver

Layer B gets eleven numbered requirements because this is where production
breaks. The ones with teeth:

**MTX-5, unreachable is not expensive.** OSRM reports an unroutable pair as
`null`. Writing it as a large finite number — 10⁹ was this repository's own
choice in three examples before it was caught — makes it an arc the solver will
*use* when nothing better exists, returning a leg no vehicle can drive.
Large-finite sentinels get optimised into solutions.

**MTX-4, snapping is data quality.** Paper 01 §5 measures a 69.9 m snap on an
ordinary San José address and a 49% candidate rejection rate on the corpus.

**MTX-6, content-addressed and versioned.**
`hash(locations, profile, osm_extract_version, bucket_scheme)`, pinned by the
plan. This is what makes INV-4 checkable at all: a plan verified against a
different matrix than it was built with is not verified.

**MTX-7/8, the n² wall.** 5,000 locations is 25M cells. Paper 02 §2 measures the
gateway's own bound: 10,000 cells at 78.9 ms. Above ~20k nodes dense matrices
are memory-prohibitive *and unnecessary* — local search only evaluates near
neighbours, so k-nearest (k ≈ 20–50) plus full depot rows loses nothing.

**MTX-10, pair-level caching.** Architecturally the interesting one. The gateway
caches by request, and for `/table` the coordinates *are* the parameters — change
one stop and the whole matrix refetches. The requirement is ≥90% pair reuse on
incremental days, and **no request-keyed cache can deliver that at any hit
rate**, because the unit of reuse is the pair. Hence a second cache above the
first. That is duplication only if you squint: they key on different things and
miss on different things. Paper 02 §3's 11.5× is what a hit is worth.

---

## 11. CON-11 — the deepest lesson here

Every constraint reaches a plan through four steps, and **the third is where it
goes wrong**:

1. The domain model carries it, in the layer owning the invariant.
2. The verifier checks it exactly.
3. **The search is told whatever can be said soundly** — often less, sometimes
   nothing.
4. What cannot be said is refused by name.

Step 3 is *a sound approximation or nothing*. An encoding admitting a violating
plan is not a partial implementation; it is **the constraint's absence wearing
its name.** Permitted answers: an orchestrator loop (solve, measure, narrow,
solve again), or a refusal naming the feature. Never a plan that violates it.

The failure mode if omitted:

> The system reliably detects illegal plans it had no way to avoid building.
> That is not a safety net; it is a defect that looks like one, because every
> part of it reports success: the model accepted the constraint, the search
> returned a plan, and only the verifier — after the fact, and after a
> dispatcher has seen it — objects.

Four constraints were found in exactly that state (skills, order-class
incompatibility, site access, depot inventory), and operator locks made five.
**None was a missing check.** Every one had a check. What each lacked was any way
for the search to avoid the plan the check would reject.

Note how §5's mutation results are the *benign* face of the same asymmetry: the
verifier catches everything, instantly, with the right name. That is exactly why
a check is never sufficient evidence that a constraint is implemented.

---

## 12. Verification and benchmark policy

Seven levels: L1 unit (100% branch on rule engines), L2 property (zero invariant
violations over 10⁵ generated instances, `make property-soak`), L3 golden
(byte-identical under CON-4), L4 public benchmarks, L5 frozen production corpus
(no regression >0.5%), L6 shadow, L7 canary.

The gate policy is stricter than most published work: gap to best-known
**at a declared budget on declared hardware** — a benchmark number without both
is meaningless — with CI blocking a merge on a 0.25 percentage-point aggregate
worsening or a 2% single-instance regression. Comparing a different objective to
published BKS is classified as a reporting defect.

`benchmarks/instances/README.md` records the current results: `E-n22-k4` at 375
and `RC208` at 776, both matching published optima, and `pr107` at 44,303 in
0.7 s, matching TSPLIB's optimum. Best-known values are read from each instance's
own `COMMENT` or sibling `.sol`, never transcribed into code, because a
hand-typed registry is a registry of typos.

And the targets carry a disclaimer most projects would omit: *these are targets,
not claims — they MUST be replaced by measured baselines in
`benchmarks/BASELINE.md` before any external communication.*

---

## 13. Where it actually stands

**69 of 73 backlog tasks are done.** Three are open (`T-85` insertion quotes,
`T-86` bounded intra-run parallelism, `T-88` an anonymisation gate) and one is
blocked (`T-84`, compiling a `Problem` into cuOpt, which needs a GPU to
demonstrate).

> §13's summary prose still reads "61 of 65 done … the four that remain" and its
> table lists only `T-84`, while three rows below are marked `open`. The counts
> moved with amendments 2.0–2.7 and the prose did not. Worth a one-line fix.

Two things to know before drawing conclusions.

**The platform is a library, not a service.** `vrp/api.py` implements `/verify`
transport-agnostically and says why: how the Rust gateway reaches a Python
solver is an open architectural question the SDD does not answer, and settling it
inside a task that did not need it would have buried the decision. So the rich
model — time windows, driver hours, multi-dimensional capacity, prizes,
synchronisation, electric range — is built, tested, and unreachable over HTTP.
The `/vrp` you can call is the gateway's heuristic, whose cost §3 now measures at
8–15%. **This seam is the largest open architectural question in the
repository**, and §3 and §4 together are the business case for closing it.

**[`VRP_SDD_FIT_GAP.md`](../planning/VRP_SDD_FIT_GAP.md) is stale in a dangerous
direction.** Dated 2026-08-25, it reports "none of 29 functional requirements
fully met" — true of the *gateway's* `/vrp`, written before most of `vrp/`
existed. Read as a verdict on the system it is badly wrong.

---

## 14. What to take from this

Five patterns, none specific to routing:

1. **Separate what produces an answer from what certifies it** — different
   package, different author, no shared imports — then make the certifier's
   interface public, so independence is structural rather than promised.
2. **Keep one deliberately slow, obviously-correct implementation** as ground
   truth and check the fast one against it continuously. §3's 0.9 m agreement
   across two languages is what that buys.
3. **Never compare self-reported scores.** Re-score every candidate on one scale
   you control. Without this, §3 could not have been measured at all.
4. **A constraint your search cannot avoid violating is not implemented** — it
   is absent, and detection after the fact is a defect that reports success at
   every stage but the last.
5. **Measure the thing you are about to defend.** The hysteresis band
   (Paper 02 §4) turned out nearly inert at its shipped default, and the sweep
   heuristic turned out to cost 8–15%. Neither was knowable from the source.

---

## 15. Reproducing this report

```sh
export WHITEPAPER_GATEWAY=http://10.211.55.33:8000
cd docs/whitepapers/experiments
PYTHONPATH=../../.. uv run python e02_heuristic_vs_solver.py   # §3
PYTHONPATH=../../.. uv run python e03_anytime.py               # §4
PYTHONPATH=../../.. uv run python e06_mutation.py              # §5
```

Every run is seeded and pins its matrix, so results reproduce on the same corpus
and map extract. A different extract moves the road distances and therefore the
gaps — which is why each result file records the gateway it was measured
against.

### Reading order in the source

| Path | Role |
|---|---|
| [`vrp-spec-driven-development.md`](../vrp-spec-driven-development.md) | Constitution, requirements, plan, backlog |
| [`TDD/vrp-catalogue-v2.1.md`](../TDD/vrp-catalogue-v2.1.md) | 157 real-world scenarios grounding the requirements |
| `vrp/model.py` → `vrp/evaluator.py` → `vrp/verify/verifier.py` | The spine: model, ground truth, gate |
| `vrp/matrix.py`, `vrp/osrm.py` | Layer B, where production breaks |
| `vrp/portfolio.py`, `vrp/lns.py`, `vrp/decompose.py` | Layer D, the replaceable part |
| `vrp/epochs.py`, `vrp/stability.py` | Layer G — churn priced as a Tier-6 term, because a 0.5% gain that reshuffles half the plan at 14:00 is a net loss |
| `vrp/adherence.py`, `vrp/calibrate.py` | Layer H, the loop back to reality |
