# 24 — Dynamic dispatch: same-day operation

*Tier 2: authored from `vrp/epochs.py`, `policies.py`, `icd.py`,
`pcdispatch.py`, `replay.py`, `committed.py`, `triggers.py`, `stability.py`,
`quote.py`, `jobs.py`.*

## The premise

Same-day and on-demand operations are **not static problems solved repeatedly**.
They are sequential decision problems under uncertainty: at each epoch the
system sees the requests known so far and must decide which to **dispatch now**
— committing them to routes — and which to **postpone** so they can be
consolidated with requests that arrive later.

That framing drives everything below. Hand every request to epoch 0 and the
whole apparatus collapses into a static solve wearing a costume: every policy
scores identically, because there is nothing left to consolidate.

## Must-go classification, and which way to be wrong

Some requests are **must-go**: postponing them makes their time window
unreachable. The classifier is *conservative by construction*, and the
asymmetry is the design:

> Calling a deferrable order must-go costs a little consolidation — the van
> goes out slightly emptier than it might have. Calling a must-go order
> deferrable costs a delivery that never happens, and the customer finds out
> before the dispatcher does.

So **every uncertain case resolves to must-go**: no fleet, no matrix entry, no
way to tell. The guarantee is that the system never postpones a must-go order.

## Four dispatch policies, and a denominator

| Policy | Dispatches | Role |
|---|---|---|
| **Greedy** | everything known now | ceiling on service, floor on consolidation |
| **Lazy** | must-go only | the floor; "dreadful consolidation, and exactly the point" |
| **Random(p)** | must-go plus each other request with probability p | spans the two; at p=0 and p=1000 it *is* them, which is a useful check |
| **ICD** | consensus across sampled futures | the recommended default |
| **Prize-collecting** | whatever the solver chose to serve | jointly decides dispatch and routes |

The three baselines are "the competition-standard baselines and MUST be
retained permanently as the denominator for every policy claim". *Denominator*
is the load-bearing word: a dispatch policy that beats nothing in particular
has not been shown to be any good. `BASELINES` is a registry rather than three
loose functions, so the replayer enumerates it and "a baseline that quietly
stopped being compared would take every claim made against it with it".

### Iterative conditional dispatch (the default)

The idea in one line: **an open request should go now if waiting would not buy
it company.**

Sample plausible futures; in each, ask whether this request would still have
been sent; let the ones that keep coming back go. A request is "dispatched" in
a scenario when postponing would leave it travelling alone — no sampled future
arrival close enough to consolidate with — or when the wait would breach its
window. Thresholds are applied iteratively across rounds.

Chosen as the v1 default because it needs no labelled data, no training
pipeline, and degrades gracefully, while coming close to learned approaches on
the competition instances.

### Prize-collecting dispatch

What separates this from ICD is the word **jointly**. ICD decides a dispatch
set and hands it to a router. Here the router decides both at once, "because
whether a request is worth sending now depends on the route it would join, and
that is exactly what a solver already computes".

The mechanism needs no new machinery: an order is droppable when it carries a
prize and sits above priority tier 0, and the `PRIZE_COLLECTING` objective mode
puts forgone prize and routing cost in one currency. An epoch becomes: must-go
work required, deferrable work priced, solve — and whatever the solver chose to
serve *is* the dispatch set. "The dispatch question turns out to be a shape the
objective already had."

## The replayer: the gate everything else stands on

`vrp/replay.py` replays historical days epoch by epoch so policies can be
evaluated offline against a greedy baseline. It was built **before** the
policies that need it, because "neither claim means anything without a
measurement both are made against".

A `Day` is an **arrival schedule**, and an order is invisible until its epoch.
That single property is what makes it a replayer rather than a loop.

## Re-optimising a day already in progress

### Committed state

When a vehicle breaks down at 11:00, the system re-optimises only the affected
and nearby work while everything already executed or committed stays fixed. No
stop already visited **or currently en route** is moved.

Executed and en-route work is converted into `FIX_ROUTE_PREFIX` and
`FREEZE_UNTIL` locks. Both lock kinds and their invariant existed first; what
was missing was the thing that produces them, so "a re-optimisation at 11:00
was free to reorder the morning and nothing in the system objected".

Two details carry the requirement:

- **En route is committed.** A van three minutes from a stop has not visited
  it, and moving it means a driver turning around in the street. "A manager
  pinning only completed work passes every test written against completed
  work."
- **The freeze horizon is not the prefix.** `FIX_ROUTE_PREFIX` pins *what* each
  vehicle does next; `FREEZE_UNTIL` pins *when*. They are different locks doing
  different jobs.

### Triggers

Re-optimisation is **event driven** — breakdown, cancellation, large ETA drift,
new priority order — not on a timer.

The budget is thirty seconds with 90% of stops locked, and it is met **by not
re-solving the plan**: locked LNS over affected and neighbouring routes only.
"A re-optimisation that touched everything would be a fresh solve wearing a
different name, and it would blow the budget on any fleet worth the trouble."

What comes back is **the delta** — stops moved, cost change, new lateness — not
only the new plan.

### Churn, priced rather than merely reported

> A 0.5% cost gain that reshuffles half the plan at 14:00 is a net loss.

Two kinds, counted **separately**, because they are not equally expensive:

| Kind | Whose problem |
|---|---|
| a stop moving to another van | the driver's: an unplanned route, an address they do not know, a van that may not have the right equipment |
| a stop keeping its van but shifting an hour | the customer's: somebody was told a time and it is now wrong |

Summing them at source "would take a real decision away from the caller".

Churn enters the objective at tier 6 (doc 22). The penalty is a **curve, not a
constant**, because the right price depends on the business: "a courier network
re-planning every ten minutes and a grocery delivery with booked slots are not
the same problem". The curve is what lets an operation decide how much
stability is worth to them.

## Quotes: answering a dispatcher on the phone

Target latency is p95 ≤ 2 s for a single-order insertion or removal quote.

**A quote is not a re-plan, and that is the whole requirement.** A function
that returned a better plan with everything moved "would be answering a
question nobody asked, however good the plan and however fast it arrived —
every other stop already has a promised time, and half of them are on vans that
have left". Existing routes keep their order; one position is opened for the
candidate.

**The price is what serving the stop costs, not what not serving it costs.**
The canonical objective's unassigned penalty dominates: on a twelve-stop
instance one unserved order is worth a million against forty-five thousand of
distance, so a naive delta reports that inserting an order *saves* most of a
million. "That number is real and it is not a price."

## Jobs and idempotency

`vrp/jobs.py` carries solve-job registration, idempotency, and the anytime
incumbent — the machinery a caller needs to submit a solve, ask about it, and
retrieve the best plan found so far without waiting for the budget to expire.

## Examples worth running

| Scenario | Script |
|---|---|
| a breakdown mid-morning | `dynamic/breakdown_at_eleven.py` |
| committed work that must not move | `dynamic/committed_state.py` |
| dispatch waves | `dynamic/dispatch_waves.py` |
| the churn trade-off curve | `dynamic/churn_tradeoff.py` |
| quoting an insertion | `dynamic/insertion_quote.py` |
| policies against the replayer | `dynamic/replay_policies.py` |
| prize-collecting epoch | `dynamic/prize_collecting_epoch.py` |
| preemption | `dynamic/preemption.py` |
