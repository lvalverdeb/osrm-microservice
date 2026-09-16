# 25 — Hours of service

*Tier 2: authored from `vrp/hos/rules.py`, `schedule.py`, `tachograph.py`, and
the verifier's INV-7.*

Working-time law is a hard legal constraint with criminal and licensing
consequences. The module is therefore "deliberately literal: each rule set
states the regulation's numbers as named constants and nothing infers them."

## The interface

```python
init_state(carry_over)        -> DriverState
can_drive(state, seconds)     -> bool
advance(state, activity, sec) -> DriverState
required_break(state)         -> Break | None
remaining_drive(state)        -> int
```

`DriverState` is **immutable** and `advance` returns a new one. That is what
lets a scheduler explore a route without unwinding mutations, and it is why the
accumulators are plain integers rather than a class with methods.

Rule sets are selected per vehicle via `Vehicle.hos_rules` (EU-561, US-HOS).

## Breaks are scheduled inside route evaluation, never after

This is the design decision the section is emphatic about, and the symptom of
getting it wrong is named: routes that were feasible before breaks and
infeasible after, "showing up as a plan that *loses* its last two stops per
route on publication".

The difference is structural rather than a matter of care:

| Post-hoc pass | This implementation |
|---|---|
| compute arrivals, then insert breaks between them | the clock and the driver state advance **together** |
| reported arrivals are the ones computed *before* breaks existed | a break at hour four pushes every later arrival by its duration |
| a stop that no longer fits silently disappears | it is visible as a violation |

## Breaks are taken as late as the rules allow

For a single break duration this is **optimal** — driving longer before the
first break can never require more breaks — and it is what a driver actually
does.

A leg longer than the driving allowance is **split mid-arc**, which is why
`Placement.ANYWHERE_ON_ARC` is the default.

## What is deliberately refused

**EU's split break (15 min then 30 min) is not attempted.** It is a genuine
dynamic-programming problem, and the module refuses rather than approximating,
"because a break plan that is nearly legal has no value".

**Breaks requiring a qualifying facility are not placeable.** That needs
facility candidates in the matrix, which is the unfinished half of this work.

**Only the single duty is modelled.** EU's weekly (56 h) and fortnightly (90 h)
limits, the twice-weekly extension to 10 h, and the reducible daily rest all
need a horizon longer than one route, which this planner does not have. They
are carried as `week_drive_used` so a day can be **refused** against an
envelope already partly consumed, but they are not *planned* across days.
"Refusing to plan is the safe direction: the failure mode of pretending
otherwise is a legal one." The system declines rather than producing a day that
is legal only if you do not look at the week.

## Carry-over: the tachograph is authoritative

Where tachograph or ELD data is available it is the authoritative source for
`initial_state`, and `vrp/hos/tachograph.py` reads a driver's recorded day into
a `DriverState` so the carry-over "stops being a number somebody works out by
hand".

**The hard part is scope, not arithmetic.** A driver who drove nine hours
yesterday, slept eleven, and has driven two this morning has consumed **two**
hours, not eleven. Everything before a qualifying daily rest belongs to a
finished duty and must not be charged against today's envelope — "a reader that
summed the whole feed would refuse to plan a day that is entirely legal".

**What qualifies is per rule set, and it is not a detail.** EU-561 wants 11
hours of daily rest; US-HOS wants 10. A ten-hour break resets the day for an
American driver and does not for a European one, so the same feed produces two
different states depending on which law the driver is under. `read_duty`
therefore takes the rule set.

Records are folded through the rule set's own `advance()`, never counted in the
reader: "Two implementations of *what does a DRIVE record do to a driver* would
be two chances to disagree."

## How the verifier checks it

INV-7 is evaluated whenever a vehicle declares a rule set, and reported *not
applicable* only when none does — never "passed".

The verifier imports the **rule sets** but never the scheduler. The rules are
shared reference data in the same sense as the domain types — both sides must
agree what EC-561/2006 Art. 7 says or they cannot discuss the same duty —
"whereas the scheduler is the thing under judgement".

Crucially, the driver state is **rebuilt from the timeline's own arrival and
departure stamps**, so a scheduler that miscounts its driving hours is caught
rather than confirmed.

## Try it

`examples/src/fleet/rich/hours_of_service.py` — "A long-haul day under EU and
US driving law, and what the law costs."
