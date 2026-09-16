# 28 — Explainability and diagnosis

*Tier 2: authored from `vrp/diagnose.py`, `vrp/explain.py`, `vrp/allocate.py`.*

## Why this is a first-class feature

> Dispatchers reject plans they cannot explain, and unexplainable plans are
> silently overridden — which destroys the benefit.

So every plan must answer, per order: *why was I assigned to this vehicle, in
this position, at this time?* And every rejection must answer: *which
constraint made me infeasible, and what would have to change?*

The bar is explicitly **not** "an explanation exists". "Time window problem" is
an explanation and it is useless. The standard is set by example:

> Earliest arrival 14:12 from nearest eligible vehicle V-11; window closes
> 13:30.

— because it names the vehicle to look at and the two numbers to compare.

## Three layers, deliberately separate

| Layer | Question | Module |
|---|---|---|
| Pre-flight diagnosis | is this order servable **at all**? | `vrp/diagnose.py` |
| Explanation | what would have to change? | `vrp/explain.py` |
| Allocation report | what did each deployed vehicle do, and what was it worth? | `vrp/allocate.py` |

## Pre-flight: reasons are produced, never inferred

> Each reason MUST be produced by an explicit diagnostic pass, not inferred.

The failure this prevents is worth quoting in full, because it is the reason
the rule is absolute:

> A solver that cannot place an order knows only that it could not. Turning
> that into `CAPACITY_EXCEEDED` by looking at what seems likely is how a
> dispatcher is told the wrong thing with total confidence, spends an afternoon
> finding a larger van, and discovers the real problem was a tail lift.

**Pre-flight means before any solve, and one order at a time.** The question is
whether a single order is servable by *any* vehicle, ignoring every other
order. That narrowness is what makes the answer trustworthy: "it depends on
nothing that a search might have done differently".

An order that passes pre-flight can still go unassigned — **that is a different
code with a different fix**, and conflating the two is the error the separation
exists to prevent.

Six of the ten specified codes are decidable this way. The other four are
declared in `UNIMPLEMENTED` **with their reason**, "because a caller waiting
for a code that never arrives has no way to tell *not applicable* from *not
built*".

## Explanation: `would_fit_if` is the harder half

A reason code says what went wrong. `would_fit_if` says what to do, as a
concrete edit to the instance: widen *this* window to *this* instant, raise
*this* capacity to *this* figure.

> A dispatcher handed a diagnosis and no prescription still has all the work in
> front of them.

**Nothing here re-derives a reason.** Pre-flight owns the reason codes and owns
the rule that they come from an explicit pass; this layer turns each code into
an actionable edit and stops where pre-flight stops.

## Allocation reporting

For every deployed vehicle: utilisation on each capacity dimension, duty time
used against available, and the marginal cost of removing it.

Allocation is **not a separate solve**. Deployment is endogenous — each vehicle
carries its own fixed cost, charged only when used, so the search decides the
fleet while it routes (doc 22). What remains is the part a dispatcher reads.

**Everything is recomputed, never read off the plan.** The reasoning names the
specific hazard:

> an allocation block is exactly the sort of output nobody checks — it is
> prose, it looks authoritative, and it is where a plausible-but-wrong
> utilisation figure would live for years.

So loads come from a rebuilt timeline rather than from the plan's `load_after`,
and duty from the recomputed timeline rather than from its arrival stamps. This
is INV-9's rule applied to reporting.

**Marginal value takes its re-solver as an argument**, because the definition
is "the objective delta from re-solving with that vehicle removed" — and a
report that hard-coded which solver to use would bake a choice into a reporting
module.

## Where a caller meets this

- `/vrp` responses quote pre-flight codes when reporting an unassigned order.
- `examples/src/fleet/explain/preflight_diagnosis.py` — the codes.
- `examples/src/fleet/explain/why_unassigned.py` — the prescription.
- `examples/src/fleet/alloc/*.py` — the allocation reports.

## Related

Infeasibility caused by **operator locks** has its own diagnosis path: the
minimal conflicting lock set, never a silently dropped lock (doc 23).
