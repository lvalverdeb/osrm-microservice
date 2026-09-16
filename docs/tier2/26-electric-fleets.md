# 26 — Electric fleets and charging

*Tier 2: authored from `vrp/battery.py`, `vrp/electric.py`, and INV-16.*

## The curve is the requirement

A battery that charged at a constant rate would make charge time a division and
the whole feature decorative. Real cells take current fast while empty and
taper near the top, "which is why a driver charges to eighty percent and drives
on rather than waiting for a hundred".

> A model without the taper cannot prefer the shorter stop, so it plans the
> wrong one and is confidently wrong about when the van gets back.

`ChargingCurve` is therefore piecewise: `charge_seconds` walks band boundaries,
charging at the rate in force in each. It is the same shape as time-dependent
travel (doc 27) — piecewise-constant rates over a quantity that advances — and
both stay in **integers**, because the reproducibility constraint forbids
accumulating floats.

## Rounding is pessimistic in both directions

Charging rounds **up** and consumption rounds **up**, so the model is
pessimistic about time on the plug and pessimistic about energy off it.

> A van that arrives with more charge than promised is a good surprise; the
> other kind is a recovery.

## Why charging is an orchestrator, not a solver constraint

An electric instance is **refused by the PyVRP adapter by name** and handled by
`vrp/electric.py` instead. The reason is structural: PyVRP compiles capacity
dimensions, not "a state that a *detour* replenishes on a non-linear curve".

So charging joins the other whole-route constraints that live as orchestrators
around the search — depot inventory (`vrp/depots.py`) and synchronisation
(`vrp/synchronise.py`) — under one design rule:

> the model carries the constraint, the verifier checks it independently
> (INV-16), the search is told what can be said soundly, and what cannot be
> said is refused by name.

`NoChargerReachable` is that refusal, and the reasoning for raising rather than
coping is operational:

> A plan that silently dropped the stop it could not reach, or that charged at
> a customer's doorstep, would both be worse than being told the fleet is wrong
> for the round — a dispatcher can hire a diesel van in ten minutes and cannot
> un-strand a driver.

## Where to charge: greedy, and not claimed to be optimal

The van charges **as late as it can** — at the last charger it can still reach
before the battery would go flat.

Late is better than early on a tapering curve, because a battery that arrives
emptier takes current faster. It is the same reason a driver runs down to
twenty percent before stopping.

**This is a repair, not the electric VRP.** Choosing charge points and charge
amounts *jointly with the route* is a search problem; this module states
plainly that it does not attempt it.

## The fields

| Field | Meaning |
|---|---|
| `Vehicle.battery_wh` | capacity; `None` means not electric |
| `Vehicle.consumption_wh_per_km` | draw per kilometre |
| `Vehicle.charger_locations` | where charging may happen |
| `Vehicle.charging_curve` | the piecewise rate schedule |
| `Vehicle.initial_soc_ppt` | state of charge at shift start, in parts per thousand |

Charger locations are **geography, not operation**: the delivery-model contract
(doc 06) deliberately excludes them, because "a model that carried it would go
stale the day somebody installs one".

## What the code is candid about

FR-20 is a *could*-level requirement and "the only requirement in the backlog
with no data source in this stack: nobody here has charger locations or a
manufacturer's charging curve". The arithmetic, the invariant and the planner
are real; the inputs are supplied by the caller and nothing claims they
resemble a particular vehicle.

## Try it

`examples/src/fleet/rich/ev_recharging.py` — "A van that has to stop for
electricity, and a plan that knows when."
