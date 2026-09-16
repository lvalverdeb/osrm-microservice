# 23 — Constraint coverage: what this system can model

*Tier 2: authored from `vrp/model.py`, `vrp/verify/verifier.py`, the module
docstrings, and the example catalogue.*

The question this answers is "can it express my operation?". Every row names
the domain field that carries the constraint, the invariant that enforces it,
the module that implements it, and a runnable example.

Field lists are generated in [doc 05](../tier1/05-domain-model.md); this
document is the map from business language onto them.

## Problem shapes

`Problem` composes into the standard variants rather than naming them: TSP,
CVRP, VRPTW, MDHVRPTW, PDPTW, DARP, IRP, LRP, and multi-trip variants of each.
`models/*.json` carries `problem_id` — e.g. `mdhvrptw` for mixed parcels — but
that is a label for the delivery model, not a solver mode.

## Load and fleet

| Constraint | Carried by | Invariant | Example |
|---|---|---|---|
| Multi-dimensional capacity (weight *and* volume *and* pallets) | `Vehicle.capacities`, `Order.quantities` — both `dict[str, int]` | INV-3 | `rich/multi_capacity.py` |
| Heterogeneous fleet | per-vehicle capacities, costs, `profile` | — | `rich/heterogeneous_fleet.py` |
| Multiple depots | `Vehicle.start_location_id` / `end_location_id` | — | `alloc/fleet_mix.py` |
| Open routes (no return leg) | `Vehicle.open_route` | INV-6 | `rich/heterogeneous_fleet.py` |
| Fixed cost, per-metre, per-second, per-order, overtime | five `Vehicle` cost fields | INV-9 | `fleet/objective_modes.py` |
| Fleet minimisation | objective mode | — | `alloc/fleet_minimisation.py` |
| Reloads / multi-trip | `reload_locations`, `max_reloads`, `reload_duration` | INV-11 | `rich/multi_trip.py` |
| Depot inventory | `Location.inventory` | INV-13 | `alloc/depot_inventory.py` |
| Dock capacity | `Location.dock_capacity` | INV-12 | `rich/multi_trip.py` |

A van is full when **any** dimension runs out — the example's subtitle is
"totals are the wrong test".

Depot inventory is enforced **globally, during search**, not as a pre-assignment
step. `vrp/depots.py` records the gap it closed: the diagnostician reported
`DEPOT_STOCKOUT` before the solve and the verifier reported the over-draw
afterwards, "and in between the search drew whatever it liked". Assigning
orders to depots first and routing within the assignment is explicitly the
wrong answer.

## Time

| Constraint | Carried by | Invariant | Example |
|---|---|---|---|
| Time windows, multiple per stop | `StopSpec.time_windows` (sorted, disjoint) | INV-4 | `tw/multiple_windows.py` |
| Soft windows with earliness/lateness cost | `TimeWindow.hardness`, `*_cost_per_sec` | tier 5 | `tw/sla_windows.py` |
| Service time, fixed plus per-unit | `StopSpec.service_fixed`, `service_per_unit` | INV-4 | `tw/envelope_round.py` |
| Per-vehicle service speed | `Vehicle.service_factor_ppt` | INV-4 | — |
| Driver shift | `Vehicle.shift`, `max_duration` | INV-5 | `rich/hours_of_service.py` |
| Release times | `Order.release_time` | INV-4 | `dynamic/dispatch_waves.py` |
| Maximum ride time | `Order.max_ride_time` | INV-14 | `rich/ride_time.py` |
| Time-dependent travel | `Problem.speed_profile(s)` | INV-4 | `rich/time_dependent.py` |
| Departure scheduling | polish pass | — | `rich/departure_scheduling.py` |

Ride time is not a window, and the example says so: "How long it may be aboard
is not the same as when it may be dropped." A delivery window says when the
drop may happen; a ride time says how long the journey may take, and an
instance can need both.

## Who may serve what

| Constraint | Carried by | Invariant | Example |
|---|---|---|---|
| Driver/vehicle skills | `Order.required_skills`, `Vehicle.skills` | INV-10 | `rich/skills_and_access.py` |
| Site access class (weight, height, permit) | `Vehicle.access_class` + `gross_weight_kg`, against `Location.access_classes` + `max_vehicle_kg` | INV-10 | `rich/skills_and_access.py` |
| Order-to-order incompatibility | `Order.order_class`, `incompatible_with` | INV-10 | `rich/skills_and_access.py` |

Incompatibility is expressed **as classes, not order ids**: "foodstuff must not
share a compartment with hazardous goods" is a statement about kinds. The
specification requires incremental class-count tracking per route precisely
because pairwise checking is quadratic per move.

## Priority and declining work

| Constraint | Carried by | Objective effect | Example |
|---|---|---|---|
| Priority tiers | `Order.priority_tier` | lexicographic; higher tier never sacrificed | `rich/prizes_and_priority.py` |
| Priority **source** | `Order.priority_source` (`STATUTORY` / `CONTRACTUAL` / `COMMERCIAL`) | ordered, expire differently, only one negotiable | `rich/priority_sources.py` |
| Prizes / droppable orders | `Order.prize` | `PRIZE_COLLECTING` merges tier 2 into money | `rich/prizes_and_priority.py` |

A `STATUTORY` order may not carry a prize — the model refuses it on
construction, because "a prize is the price at which declining is acceptable,
and there is none". Three tiers with different clocks are three different
constraints, not three weights on one.

## Pickup and delivery

`Order.kind` is `JOB` or `SHIPMENT`. A `SHIPMENT` needs both a pickup and a
delivery; a `JOB` needs exactly one. That single field is what makes PDPTW and
DARP expressible. Ride-time limits apply to shipments only — a job has one stop
and its elapsed time is its service duration.

## Coupling between routes

| Constraint | Carried by | Invariant | Example |
|---|---|---|---|
| Synchronisation (two routes must meet) | `Problem.synchronisations` | INV-15 | `rich/synchronisation.py` |
| Operator locks | `Problem.locks` | INV-8 | `rich/locks_and_overrides.py` |

Synchronisation is implemented as a **loop, not a constraint**: the
second-echelon departure depends on the first echelon's arrival, which is a
constraint across two routing problems, and PyVRP has no construct relating two
routes' timelines. So: solve, observe when the first half actually happened,
tell the second half it may not start before that, solve again.

Locks are a first-class input. If locks make the instance infeasible the system
returns `INFEASIBLE` **with the minimal conflicting lock set** (an IIS-style
diagnosis) and never silently drops one — "there are two ways to fail a
dispatcher here and only one of them looks like a failure".

## Energy

| Constraint | Carried by | Invariant | Example |
|---|---|---|---|
| Battery capacity and consumption | `Vehicle.battery_wh`, `consumption_wh_per_km` | INV-16 | `rich/ev_recharging.py` |
| Charging locations and curve | `charger_locations`, `charging_curve`, `initial_soc_ppt` | INV-16 | `rich/ev_recharging.py` |

See [doc 26](26-electric-fleets.md).

## Regulated driving

| Constraint | Carried by | Invariant | Example |
|---|---|---|---|
| Hours-of-service rule set | `Vehicle.hos_rules` (EU / US) | INV-7 | `rich/hours_of_service.py` |
| Hours already used today | `Vehicle.initial_state` | INV-7 | `rich/hours_of_service.py` |

See [doc 25](25-hours-of-service.md).

## Across days

| Constraint | Carried by | Module | Example |
|---|---|---|---|
| Recurring visits, visit frequency, permitted-day patterns | horizon-level | `vrp/periodic.py` | `rich/multi_period.py` |
| Territory consistency | — | `vrp/consistency.py` | `alloc/territories.py` |
| Workload fairness | objective tier 6 | `vrp/consistency.py` | `alloc/territories.py` |
| Arrival-time consistency | — | `vrp/consistency.py` | `alloc/territories.py` |

The unit of optimisation for periodic problems is the horizon, not the day:
"optimising each day independently makes the cycle infeasible", and "a locally
optimal Tuesday leaves an infeasible Wednesday".

Consistency is not treated as a concession. Drivers who serve the same
territory accumulate tacit knowledge — access codes, parking, receiving-bay
habits — which reduces service time and errors; it is "a genuine cost saver".
The three measures are kept apart because they move apart.

## Dynamic operation

Epochs, committed state, triggers, churn, replay and dispatch policies are
covered in [doc 24](24-dynamic-dispatch.md).

## Scale

| Regime | Mechanism |
|---|---|
| up to ~2,000 stops | monolithic search over a stored matrix |
| 2,000–10,000 | decomposition (`vrp/decompose.py`), `PlanarMatrix` |
| gateway `/vrp` | `VRP_MAX_STOPS` = 2,000, chunked, one TSP per vehicle |

## What is not modelled

Read this as the honest complement to the table above:

- **Stochastic travel times.** Everything is deterministic against a pinned
  matrix.
- **Simultaneous pickup-and-delivery capacity interleaving** beyond what
  `SHIPMENT` expresses.
- **Carbon or emissions objectives.**
- **Cross-docking / transfers between vehicles**, other than the
  synchronisation loop.
- **Driver preferences or rostering.** Consistency measures territory and
  fairness, not who wants which route.
