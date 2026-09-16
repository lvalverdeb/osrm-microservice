# 21 — Invariants and independent verification

*Tier 2: authored from `vrp/verify/verifier.py`, `vrp/api.py`,
`vrp/evaluator.py`, `tests/vrp/test_independent_verifier.py`.*

The verifier answers one question — **is this plan legal?** — and its value
comes entirely from *how* it answers: independently of whatever produced the
plan.

## Independence is a code-level property, not a claim

`vrp/verify/verifier.py` shares **no code** with the evaluator or any solver.
Every number is recomputed from the raw step sequences and the matrix pinned in
the problem.

The argument: "a solver graded by the arithmetic it already used will agree
with itself, and the class of bug this exists to catch — drift between an
incremental move evaluator and ground truth — would pass unnoticed."

This is enforced rather than asserted.
`test_the_verifier_does_not_import_the_evaluator` reads this file's imports. So
the boundary cannot erode quietly through a convenience import.

**What may be shared, and why:**

| Shared | Not shared |
|---|---|
| domain types (`Problem`, `Step`, `Route`) — data definitions; both sides must agree what a `Step` is or they cannot discuss the same plan | the evaluator, any solver, the scheduler |
| HoS **rule sets** — reference data; both sides must agree what EC-561/2006 Art. 7 says | the HoS **scheduler**, which is the thing under judgement |

For hours of service the driver state is rebuilt from the timeline's own
arrival and departure stamps, "so a scheduler that miscounts its driving hours
is caught rather than confirmed".

## The invariants

Sixteen, checked in this order:

| Invariant | Checks | Applicability |
|---|---|---|
| INV-1, INV-2 | coverage: every order served, exactly once | always |
| INV-3 … INV-6 | per route: capacity, time windows, shift, matrix version | always |
| INV-7 | hours of service | only when a vehicle declares a rule set |
| INV-8 | locks honoured | only when the problem declares locks |
| INV-9 | the reported objective matches a recomputation | always |
| INV-10 | skills, order-to-order classes, site access | always |
| INV-11 | reloads happen where stock is, no more often than allowed | always |
| INV-12 | dock capacity | always |
| INV-13 | depot inventory | only when a location declares inventory |
| INV-14 | maximum ride times | always |
| INV-15 | synchronisation between coupled routes | always |
| INV-16 | battery never runs flat; charging only at chargers | always |

The specification defines nine. The verifier enforces sixteen, and the numbering
past INV-9 is deliberate — the extra seven were added as the constraint model
grew. An audit once found the document defining nine while the verifier enforced
fifteen, six of which existed only in code; `tests/test_traceability.py` now
fails when the specification understates what the verifier checks.

## Not applicable is never "passed"

An invariant with no subject is reported **not applicable**, and the
distinction is load-bearing:

> Returning "ok" for an invariant that was never evaluated is a lie that
> survives until someone ships an illegal duty timeline.

So a problem where no vehicle declares hours of service reports INV-7 as
not applicable, not as satisfied. The report carries three outcomes —
violations, invariants passed, invariants not applicable — and a consumer that
collapses the last two loses exactly the information that matters.

## The report

```
Report
├── violations: list[Violation]   invariant, detail, vehicle_id, order_id
├── not_applicable: set[str]
└── ok: bool                      no violations
```

A `Violation` names the invariant, the vehicle and the order, because "INV-3
failed" is not actionable and "INV-3 [VAN-2/ORD-117] load 340 exceeds capacity
300 after stop 4" is.

## The public `/verify` contract

`vrp/api.py` exposes verification as a public endpoint, and the reason is not
convenience:

> `/verify` is deliberately public: it lets integrators check plans produced
> elsewhere, and it forces the verifier to be genuinely independent of the
> solver.

A verifier that can only check plans its own solver produced is not
independent — it shares the solver's assumptions about what a plan looks like,
and those assumptions are where a solver bug hides. **Accepting a plan from a
system that shares no code with ours is what proves the boundary is real.**

### Parsing strictly is the feature

An external plan arrives as JSON from a system with its own types, rounding and
optionality. The temptation is to be helpful: infer a missing arrival, coerce
`"600"` to `600`, default an absent window. The module refuses, and says why:

> Every one of those produces a report about a plan the integrator did not
> send — and it would pass, which is worse than failing.

So the parser refuses and names the field.

`VerificationError` is deliberately distinct from a *failed* verification.
"Your plan is invalid" and "your request is malformed" are different answers,
and an integrator acts on them differently — one is a bug in their planner, the
other a bug in their client.

The response carries `ok`, `checked_by` (`verifier@1.0.0`), `hard_violations`,
`soft_violations`, `invariants_passed` and `not_applicable`.

## Where verification sits in the pipeline

- **The portfolio** gives the verifier a veto over the winner: feasibility
  outranks optimality, so a cheap illegal plan is a defect, not a win (doc 20).
- **The route pool** checks each column before admission — but that check is
  the constructor-side predicate, which shares the timeline builder with the
  plan. "Admission is a construction decision; the plan is not." The finished
  recombination is still judged by the independent verifier.
- **The property suite** generates instances and checks INV-1…INV-9 across
  them; `make property-soak` runs 10⁵ generated instances.
- **The benchmark gate** records whether each baseline instance was verified,
  not just what it cost (doc 13).

## Known limitation

`vrp/api.py`'s window parser "still drops both soft-window cost rates today" —
recorded in `vrp/servicemodel.py` as a live example of the hand-written
field-list hazard. A plan submitted to `/verify` with soft windows is therefore
checked against windows whose penalty rates were not carried through.
