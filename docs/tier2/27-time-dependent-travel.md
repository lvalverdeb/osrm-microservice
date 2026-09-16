# 27 — Time-dependent travel

*Tier 2: authored from `vrp/timedependent.py`, `vrp/speedfit.py`,
`vrp/zones.py`.*

## One distinction is the whole module

The requirement forbids the obvious implementation in the same breath as it
states the goal:

> Per-arc (or per-zone) piecewise-constant **speed** profiles over time
> buckets. Piecewise-constant *travel time* per bucket violates FIFO and MUST
> NOT be used; the Ichoua–Gendreau–Potvin construction (speed changes when a
> bucket boundary is crossed mid-arc) is the required formulation.

Bucketing **travel time** is one line shorter and lets a van that leaves at
08:59 arrive **before** one that leaves at 09:01, because the later departure
is charged a different flat rate for the whole arc.

Bucketing **speed** and changing rate mid-arc cannot do that: leaving later
means being strictly behind at every instant, so arriving earlier is
arithmetically impossible.

That is the FIFO — or no-passing — property, and it is not an argument in a
comment: `test_leaving_later_never_arrives_earlier` is a property test over it.

## How a traversal is computed

`travel` walks the arc: step to the next bucket boundary, travel at the speed
in force, carry on. Integers throughout, because reproducibility forbids
accumulating floats. `vrp/battery.py`'s charge walk is deliberately the same
shape — both are piecewise-constant rates over a quantity that advances.

## Profiles are per arc class, not global

`Problem.speed_profile` carries one profile; `Problem.speed_profiles` maps arc
classes to their own. The example puts the point plainly: **"Rush hour on the
ring road is not rush hour on a lane."** A single global multiplier makes the
motorway and the residential street slow down together, which is not what
congestion does.

## This is the evaluator, not the data

The module is explicit that it does not claim its profiles resemble a real
afternoon. Profiles are whatever a caller supplies.

Fitting them from executed routes against the engine's free-flow assumptions is
`vrp/speedfit.py`'s job, and it needs telematics this stack does not yet have.
Separating the two is what let the evaluator be built at all: the original
blocker "said there was *nothing to fit profiles against* and concluded nothing
could be done, when the FIFO property and the filter's false-negative rate —
its whole definition of done — are properties of the construction rather than
of the data".

That is a reusable lesson: a correctness property of a construction can be
tested long before anyone has production data for it.

## Related learned inputs

| Module | Fits | Needs |
|---|---|---|
| `vrp/speedfit.py` | speed profiles from executed routes | telematics |
| `vrp/zones.py` | zone-sequence priors (which zone tends to follow which) | historical routes |
| `vrp/calibrate.py` | service times per site | executed service durations |

All three are covered in [doc 31](31-calibration-and-learning.md).

## Planning around it is a separate problem

The example is titled "Knowing the traffic is not the same as planning around
it" (`rich/planning_under_congestion.py`). Evaluating a route under
time-dependent travel is arithmetic; *choosing* departure times to exploit it
is the departure-scheduling polish pass (doc 20, ALG-5), and the two are
separate on purpose.

## Try it

- `rich/time_dependent.py` — "Leaving later must never mean arriving earlier."
- `rich/arc_class_profiles.py` — per-class profiles.
- `rich/planning_under_congestion.py` — the planning half.
- `rich/departure_scheduling.py` — "Two hours of duty that nobody was paying
  attention to."
