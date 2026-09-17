# 41 — The `vrp` library API

*Tier 2: signatures introspected from the installed `vrp-platform`; every output
below is a real return value from a run against a live gateway on a Costa Rica
graph. Generated field lists are [tier 1 doc 05](../tier1/05-domain-model.md).*

The Python routing platform as a library: what you import, what you pass it, and
what comes back.

**This is not the HTTP API.** The gateway's endpoints are documented in
[ENDPOINT_GUIDE.md](42-endpoint-guide.md). This document covers the `vrp/` package
— the modules that model, solve, evaluate, verify and explain fleet routing
(counted in [tier 1 doc 08](../tier1/08-module-map.md), which is generated from
`vrp/**/*.py` and merge-gated, so it cannot drift the way a figure restated here
would). It
is a library; nothing hosts it as a service, and how the Rust gateway would
reach it is an open design question the specification names but does not answer.

**Every example below was executed.** Signatures come from introspecting the
installed package; outputs are the real return values from a run against a live
gateway on a Costa Rica graph, using coordinates from `data/deliveries_cr.json`.
Where a call raised, the real exception text is shown.

---

## 1. What is public

`vrp/__init__.py` exports nothing and only two modules declare `__all__`
(`vrp.hos` and `vrp.verify`). The public surface is therefore **the names each
module defines and documents**, reached by importing the module directly:

```python
from vrp.model import Problem, Order, Vehicle, Location, TimeWindow, StopSpec
from vrp.osrm import build_matrix
from vrp.solve.pyvrp_adapter import solve
from vrp.evaluator import evaluate
from vrp.verify import verify
```

Two packages have no re-exports at their own level and must be imported one
level deeper:

| Package | Import as |
|---|---|
| `vrp.solve` | `from vrp.solve.pyvrp_adapter import solve` / `from vrp.solve.ortools_adapter import solve` |
| `vrp.bench` | `from vrp.bench.fixtures import …` — **checkout only**, excluded from the wheel |

Names beginning with `_` are private. Anything else named in this document is
public and has a docstring in the source.

### Running it

`vrp` is a real distribution — **`vrp-platform` 0.3.1**, built with hatchling.
From this repository it installs editable, and `import vrp` then works from any
working directory:

```bash
uv pip install -e ".[dev]"
uv run python your_script.py
```

Two consequences of how it is packaged:

- **The solvers are extras.** `pyvrp` is imported at module level by its adapter
  and `ortools` inside its entry point, so a consumer that only builds and
  verifies `Problem`s needs neither compiled wheel.
- **`vrp/bench` is excluded from the wheel.** It resolves `docs/TDD/scenarios.jsonl`
  and `benchmarks/instances/` against a repository root an installed copy does not
  have. `vrp/benchmarks.py` does ship, because its reader takes the path from its
  caller. The repository's own `models/` are not in the wheel either — a
  deployment describes its own through `VRP_MODEL_PATH`.

---

## 2. Five conventions that explain most of the API

**Everything is an integer.** Money in minor units, time in seconds, distance in
metres. Floating-point accumulation across a 200-stop timeline is how an
evaluator and a verifier come to disagree, so the model refuses floats outright:
`quantities`, `service_fixed`, `priority_tier` and the rest are integer-checked
at construction.

**Objects are frozen dataclasses.** `Problem`, `Order`, `Vehicle` and the rest
are immutable and validate in `__post_init__`. An illegal object cannot be
constructed — you get `ValidationError` at the point of the mistake, not a wrong
answer later.

**`Problem` is the universal interface.** Most modules take `problem: Problem`
and nothing else of yours. That is what makes the solver replaceable: the model
knows nothing about how a route is produced, only what a legal one looks like.

**The travel matrix is pinned and versioned.** A `TravelMatrix` carries a
`version` hash of `(locations, profile, extract_version)`. A plan records the
version it was built against, so a re-imported map invalidates plans made on the
old one rather than silently changing their meaning.

**Feasibility is a gate, not a score.** Nothing in the platform publishes a plan
as feasible on a solver's word. `vrp.verify` recomputes everything from the raw
sequences and the pinned matrix, shares no code with any solver, and has the
last word.

---

## 3. The domain model — `vrp.model`

| Type | Required | Notable optional |
|---|---|---|
| `Location` | `id`, `lat`, `lon`, `matrix_index` | `dwell_overhead`, `dock_capacity`, `inventory`, `access_classes`, `max_vehicle_kg` |
| `TimeWindow` | `start`, `end` | `hardness` (`HARD`/`SOFT`), `earliness_cost_per_sec`, `lateness_cost_per_sec` |
| `StopSpec` | `location_id` | `time_windows` (a **tuple** — several disjoint windows), `service_fixed`, `service_per_unit`, `service_per_unit_dimension` |
| `Order` | `id`, `kind` | `quantities`, `pickup`, `delivery`, `priority_tier`, `prize`, `release_time`, `required_skills`, `max_ride_time`, `order_class`, `incompatible_with`, `priority_source` |
| `Vehicle` | `id`, `capacities`, `shift`, `start_location_id` | `end_location_id`, `max_duration`, `max_distance`, `skills`, `fixed_cost`, `cost_per_metre`, `cost_per_second`, `overtime_cost_per_second`, `profile`, `reload_locations`, `battery_wh`, `charging_curve`, `hos_rules`, `access_class`, `open_route` |
| `TravelMatrix` | `version`, `durations`, `distances` | `degraded` |
| `Lock` | `kind` | `order_id`, `vehicle_id`, `order_ids`, `depot_id`, `instant` |
| `Synchronisation` | `kind`, `first`, `second` | `min_gap`, `max_gap` |
| `Problem` | `id`, `locations`, `orders`, `vehicles`, `matrix` | `horizon`, `locks`, `synchronisations`, `speed_profile`, `speed_profiles` |
| `Solution` | `problem_id`, `routes` | `unassigned`, `objective_breakdown`, `status`, `degraded`, `solver` |
| `Route` | `vehicle_id`, `steps` | — |
| `Step` | `type`, `location_id`, `arrival`, `start_service`, `departure` | `order_id`, `load_after`, `rule_ref`, `placement`, `soc_after_ppt` |

Module-level helpers: `travel_between`, `service_time`, `has_skills_for`,
`may_enter`, `must_be_served`, `precedence`, `profile_for_arc`, `sla_window`.
Exceptions: `ValidationError`, `UnreachableArc`.

### `Order.kind` is `JOB` or `SHIPMENT`

Not `DELIVERY`. This is the first thing everyone gets wrong, including me:

```
vrp.model.ValidationError: unknown kind 'DELIVERY'
```

- A **`JOB`** has exactly one of `pickup` or `delivery`. Not both, not neither.
- A **`SHIPMENT`** has both, and they are constrained onto the same vehicle with
  pickup first (`INV-2`). `max_ride_time` is only legal on a `SHIPMENT`, because
  a job has one stop and its elapsed time is already its service duration.

### A prize makes an order declinable; its absence makes it required

`prize` reads like a ranking weight and is not one. `must_be_served` decides
whether a plan may leave an order out at all, and the rule is in its docstring:

> Tier 0 is must-serve whatever it is worth. Everything else is declinable once
> it carries a prize -- an order with no prize has no price at which declining
> is acceptable, so a plan must place it or report infeasible.

So the choice is between two different answers to an overloaded day:

| | Behaviour when the fleet is short |
|---|---|
| Order carries a `prize` | The solver declines the least valuable work and returns a **shorter plan** |
| Order carries no `prize` | The solver **reports `INFEASIBLE`** rather than dropping anything |

Neither is the safe default — they answer different questions. Work that is
genuinely optional wants a prize, and the shortfall shows up as unassigned
orders you can price. Work that is obligatory wants none, and the shortfall
shows up as a refusal you cannot miss.

**The failure mode is giving a prize to work that is not optional.** The plan
comes back shorter and feasible, and the missing work has to be *noticed*
rather than *reported*. A consuming repository hit exactly this decision on a
return run in September 2026 — envelopes going back to a customer are not
declinable, so the orders carry no prize, and a fleet too small for the work
reports `INFEASIBLE` instead of quietly returning fewer parcels.

Tier 0 overrides both: it is must-serve whatever prize it carries, which is how
an SLA deadline is expressed as a constraint rather than as a weight.

---

## 4. The end-to-end workflow

Everything below is one real script and its real output.

### 4.1 Build a pinned matrix — `vrp.osrm`

```python
from vrp.osrm import build_matrix, matrix_version, SnapWarning

matrix, snaps = build_matrix(
    "http://127.0.0.1:8000",          # the gateway, not osrm-routed
    [(9.9472, -84.0531), (9.909005, -84.088218), …],   # (lat, lon), your index order
    profile="driving",
    snap_threshold_m=100.0,
    extract_version="costa-rica-2026-09",
)
```

Real output for nine real Costa Rica points:

```
matrix.version              osrm:driving:7d515ffee91a7568
matrix.shape                9 x 9  (integers: seconds, metres)
matrix.durations[0][:5]     (0, 632, 787, 362, 896)
matrix.distances[0][:5]     (0, 7455, 9395, 2848, 10071)
snaps[0]                    {'location': (9.9472, -84.0531),
                             'snapped': (9.94729, -84.05293),
                             'distance_m': 21.13420758, 'name': 'Calle 55'}
snaps[1]                    {'location': (9.909005, -84.088218),
                             'snapped': (9.909005, -84.088218),
                             'distance_m': 0.0, 'name': 'Avenida 56'}
```

**`locations` is `(latitude, longitude)`** — the opposite order to the GeoJSON
and to the gateway's own JSON bodies. Matrix indices follow your list exactly,
which is what `Location.matrix_index` refers to.

**A far snap is a warning, not an error.** Past `snap_threshold_m` you get a
`SnapWarning` through the `warnings` module, because a far snap is a data-quality
problem the caller may legitimately accept. Catch it if you want to fail hard:

```python
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("error", SnapWarning)
    matrix, snaps = build_matrix(...)
```

`matrix_version(locations, profile, extract_version)` computes the same hash
without calling anything, for checking whether a cached plan is still valid.

### At hub scale, and the two things that bite there

`build_large_matrix` (in `vrp.matrix`) tiles the work when the set is past the
gateway's cell cap. Measured on a real Costa Rica night, 17 September 2026: a hub-scale set of
roughly **1,600 stops** tiles into **289 requests** and builds in **29.4 s** —
and a whole night's facilities inside a minute. Matrix build is not where a
hub-scale night is at risk.

**That figure is with the engine on the same machine.** Tiles are fetched per
request, so a deployment with the engine one hop away pays the round trip 289
times — roughly +14 s at 50 ms RTT, ~44 s total. It changes no decision, but it
is the part of the number that is environment rather than algorithm.

**A hub-scale batch exceeds `RATE_LIMIT_MATRIX` on defaults, and fails
quietly.** The shipped limit is `300/minute` and 289 tiles sits just under it; a batch
roughly twice that shed nearly half its tiles. A shed tile is not an error — `build_large_matrix`
records it as `NFR-04` degradation and returns a matrix whose missing cells carry
`UNREACHABLE`. So the failure arrives as a plan built on a road network that does
not exist, and since the sentinel is `-1` a reachability check reads the gap as
*unreachable addresses* rather than as *unfetched tiles*.

```python
matrix, snaps = build_matrix(...)
if matrix.degraded:                     # a string saying why, or None
    raise RuntimeError(matrix.degraded) # for a night's routes, refuse it
```

`NFR-04`'s degrade-rather-than-fail is right for live dispatch, where a worse
answer beats no answer. It is wrong for a batch that plans tomorrow, which is not
a worse plan but a plan for the wrong roads. **Check `degraded` yourself**;
nothing downstream will.

### 4.2 Construct the `Problem`

```python
from vrp.model import Problem, Location, Order, StopSpec, TimeWindow, Vehicle

SHIFT = TimeWindow(start=7*3600, end=18*3600)

locations = [Location(id="DEPOT", lat=9.9472, lon=-84.0531, matrix_index=0)] + [
    Location(id=d["order_id"], lat=d["latitude"], lon=d["longitude"], matrix_index=i+1)
    for i, d in enumerate(drops)]

orders = tuple(
    Order(id=d["order_id"], kind="JOB",
          quantities={"kg": max(1, round(d["weight_kg"])), "units": d["units"]},
          delivery=StopSpec(location_id=d["order_id"],
                            time_windows=(TimeWindow(start=8*3600, end=17*3600),),
                            service_fixed=d["service_minutes"] * 60))
    for d in drops)

vehicles = tuple(
    Vehicle(id=f"VAN-{n}", capacities={"kg": 25, "units": 40}, shift=SHIFT,
            start_location_id="DEPOT", end_location_id="DEPOT",
            fixed_cost=15000, cost_per_metre=1, cost_per_second=2)
    for n in (1, 2))

problem = Problem(id="sanjose-demo", locations=tuple(locations),
                  orders=orders, vehicles=vehicles, matrix=matrix)
```

One real order, as constructed:

```json
{"id": "ORD-000008", "kind": "JOB",
 "quantities": {"kg": 6, "units": 4},
 "pickup": null,
 "delivery": {"location_id": "ORD-000008",
              "time_windows": [{"start": 28800, "end": 61200, "hardness": "HARD",
                                "earliness_cost_per_sec": 0, "lateness_cost_per_sec": 0}],
              "service_fixed": 720, "service_per_unit": 0}}
```

### 4.3 Check it can be served — `vrp.diagnose`

```python
from vrp.diagnose import preflight
findings = preflight(problem)        # dict[str, Finding], keyed by order id
```

**Returns a dict, and servable orders are absent** rather than present-and-empty,
so iterating the result is iterating problems. `Finding` is
`(order_id, code, detail)`.

Real output, on deliberately broken instances:

```
preflight(order needs a skill no vehicle has)
  ORD-000008  NO_ELIGIBLE_VEHICLE  requires ['HAZMAT']; no vehicle carries it

preflight(order heavier than any vehicle)
  ORD-000008  CAPACITY_EXCEEDED    needs {'kg': 200}; largest eligible capacity 25
```

On the real instance: `{}` — every order servable.

Checks run in a fixed order and the first failure wins, because an order usually
fails several ways at once and only one is worth telling somebody about.
Eligibility first, then capacity, then timing.

### 4.4 Solve — `vrp.solve`

```python
from vrp.solve.pyvrp_adapter import solve          # solve(problem, iterations=500, seed=0)
from vrp.solve.ortools_adapter import solve as ortools_solve   # (problem, solutions=200, seed=0)

solution = solve(problem, iterations=2000, seed=0)
```

Real output:

```
solution.status      FEASIBLE
solution.routes      VAN-1: ORD-000008, ORD-000015, ORD-000038, ORD-000040, ORD-000021
                     VAN-2: ORD-000033, ORD-000030, ORD-000041
solution.unassigned  ()
solution.solver      {'solver': 'pyvrp:0.14.0', 'seed': 0, 'iterations': 2000,
                      'matrix_version': 'osrm:driving:7d515ffee91a7568'}
```

**`solution.solver` carries its own provenance**, including the matrix version it
was built against. Determinism is a constitutional requirement: same problem,
same seed, same iterations, same plan.

Other solver-side entry points, for when the adapter is not enough:
`vrp.lns.lns_search` (ruin-and-recreate, SISR), `vrp.localsearch.accelerated_search`,
`vrp.polish.polish_route`, `vrp.setpartition.select_routes`,
`vrp.decompose.partition`, `vrp.portfolio.run_portfolio`,
`vrp.fleet.minimise_fleet`.

### 4.5 Evaluate — `vrp.evaluator`

The canonical objective. Recomputes a timeline and a cost from the sequence, the
pinned matrix, and nothing else — no incremental state.

```python
from vrp.evaluator import evaluate, build_timeline, route_metrics, route_is_legal

assignment = {r.vehicle_id: [s.order_id for s in r.steps if s.order_id]
              for r in solution.routes}
ev = evaluate(problem, assignment)      # -> Evaluation(total, breakdown, timelines)
```

Real output:

```
evaluate().total      118356
evaluate().breakdown  {'distance': 57910, 'driving_seconds': 5530,
                       'waiting_seconds': 5373, 'service_seconds': 4320,
                       'earliness_penalty': 0, 'lateness_penalty': 0,
                       'vehicles': 30000, 'unassigned_penalty': 0}
evaluate().timelines  {'VAN-1': 7 steps, 'VAN-2': 5 steps}

route_metrics(VAN-1)  {'distance': 26264, 'driving_seconds': 2586,
                       'waiting_seconds': 2968, 'service_seconds': 3000,
                       'earliness_penalty': 0, 'lateness_penalty': 0}
route_metrics(VAN-2)  {'distance': 31646, 'driving_seconds': 2944,
                       'waiting_seconds': 2405, 'service_seconds': 1320,
                       'earliness_penalty': 0, 'lateness_penalty': 0}
```

Note `waiting_seconds` — 5,373 seconds of the plan is a driver sitting outside a
closed door. On a time-window instance that number is often the real story, and
no distance-based metric shows it.

**`evaluate` takes an assignment dict, not a `Solution`.** `{vehicle_id: [order_id, …]}`.
That is deliberate: the evaluator must be able to score a sequence that no
solver produced.

`build_timeline(problem, vehicle_id, order_ids)` gives the `Step` tuple for one
route; `route_is_legal(problem, vehicle_id, sequence)` is the cheap boolean.

### 4.6 Verify — `vrp.verify`

The independent checker. Separate package, no shared code with any solver,
written by a different author. This is `CON-1` made executable.

```python
from vrp.verify import verify, Report, Violation, NOT_APPLICABLE

report = verify(problem, solution)     # -> Report(violations, not_applicable)
if not report:
    raise RuntimeError(report.violations)
```

Real output on the solved plan:

```
verify() violations       ()            # the plan is legal
verify() not_applicable   ['INV-7', 'INV-8', 'INV-13', 'INV-14', 'INV-15']
bool(report)              True
```

**`Report` is truthy when the plan is legal**, so `if not report:` is the idiom.
`Violation` is `(invariant, detail, vehicle_id, order_id)`.

**`not_applicable` is a feature.** This instance declares no hours-of-service
rules, no locks, no depot inventory, no ride-time caps and no synchronisation, so
those five invariants report as not-applicable rather than silently passing.
A check that cannot fail and a check that passed look identical unless you
separate them.

### 4.7 Score against the objective hierarchy — `vrp.objective`

```python
from vrp.objective import ObjectiveSpec, Mode, Tier, score, compare, tier_scales

spec = ObjectiveSpec(mode=Mode.MIN_COST)
sc = score(problem, solution, spec)      # -> Score(values, total)
```

Real output:

```
score().values  {'HARD': 0, 'UNSERVED_P0': 0, 'UNSERVED': 0,
                 'FLEET': 30000, 'OPERATING': 68970, 'SOFT': 0, 'QUALITY': 5382}
score().total   3724105777631592
```

That 3.7-quadrillion total is the lexicographic hierarchy realised by scaling:
each tier's weight strictly dominates the maximum attainable value of every tier
below it, computed from the instance rather than hard-coded. **Do not show it to
anyone** — compare plans with `compare(left, right, spec)` and report
`score().values`, which is the part that means something.

`Mode` is `MIN_VEHICLES`, `MIN_COST`, `MIN_DURATION`, `MAX_SERVICE`,
`PRIZE_COLLECTING`. `Tier` is `HARD`, `UNSERVED_P0`, `UNSERVED`, `FLEET`,
`OPERATING`, `SOFT`, `QUALITY`.

---

## 5. Answering questions about a plan

### `vrp.explain` — why is this order here

```python
from vrp.explain import explain, explain_assignment, would_fit_if, without

explain(problem, solution)                         # -> Explanation(assigned, rejected)
explain_assignment(problem, solution, order_id)    # -> Rationale | None
would_fit_if(problem, order_id)                    # -> tuple[Change, ...]
```

Real output:

```json
{"order_id": "ORD-000015", "vehicle_id": "VAN-1", "position": 2,
 "arrival": 26883, "marginal_cost": -249,
 "because": ["VAN-1 was the vehicle carrying it, stop 2 of 5",
             "arrival 26883s, service starts 26883s",
             "serving it here costs -249 more than skipping it"]}
```

`because` is already human-readable. This is the requirement `CON-5` calls a
product feature: every plan must answer, per order, why it was assigned to this
vehicle in this position at this time.

For an order nothing can serve, `would_fit_if` returns the change that would fix
it:

```
would_fit_if(the HAZMAT order) -> [{'change': 'vehicle_skill', 'to': 'HAZMAT'}]
```

### `vrp.quote` — what one change costs

```python
from vrp.quote import quote_insertion, quote_removal, Quote, NoRoomForOrder

quote_removal(problem, assignment, order_id)      # -> Quote
quote_insertion(problem, assignment, order_id)    # -> Quote, or raises NoRoomForOrder
```

Real output — dropping a stop from VAN-1 and putting it back:

```
quote_removal    {'order_id': 'ORD-000038', 'price': -7323, 'vehicle_id': 'VAN-1',
                  'route': ('ORD-000008','ORD-000015','ORD-000040','ORD-000021')}
quote_insertion  {'order_id': 'ORD-000038', 'price':  7323, 'vehicle_id': 'VAN-1',
                  'route': ('ORD-000008','ORD-000015','ORD-000038','ORD-000040','ORD-000021')}
```

The prices mirror and the insertion found the same position. `price` is in the
canonical objective's units; negative is a saving.

**Both take the assignment dict and an `order_id` already in the problem** — they
price a change to a plan, they do not accept a foreign `Order`. To quote a new
order, add it to the `Problem` and leave it out of the assignment.

**`NoRoomForOrder` is raised rather than priced at some large number**, because a
dispatcher reads a big number as expensive and an exception as impossible.

---

## 6. Real-life scenarios

Eight operations, each a shape someone actually runs, each executed against the
same real Costa Rica corpus. The plans, the numbers and the exception texts below
are what came back.

### 6.1 Grocery delivery — the customer chose a two-hour slot

The window is the whole problem. Eight orders, each with the slot the customer
picked at checkout, two vans, an eight-minute doorstep.

```python
from vrp.model import Problem, Order, StopSpec, TimeWindow, Vehicle
from vrp.solve.pyvrp_adapter import solve
from vrp.evaluator import evaluate, build_timeline

orders = tuple(
    Order(id=d["order_id"], kind="JOB", quantities={"crates": d["units"] // 2},
          delivery=StopSpec(location_id=d["order_id"],
                            time_windows=(TimeWindow(start=a*3600, end=b*3600),),
                            service_fixed=8*60))
    for d, (a, b) in zip(drops, slots))

vans = tuple(Vehicle(id=f"VAN-{n}", capacities={"crates": 20}, shift=DAY,
                     start_location_id="DEPOT", end_location_id="DEPOT",
                     fixed_cost=20000, cost_per_metre=1, cost_per_second=2)
             for n in (1, 2))

problem  = Problem(id="grocery-slots", locations=LOCS, orders=orders,
                   vehicles=vans, matrix=MATRIX)
solution = solve(problem, iterations=3000, seed=0)
```

Both vans were available. The solver used **one**, and every order landed inside
its promised window:

| Order | Arrives | Service starts | Promised | Waited |
|---|---|---|---|---|
| ORD-000008 | 06:10 | 08:00 | 08:00–10:00 | 109 min |
| ORD-000015 | 08:13 | 08:13 | 08:00–10:00 | — |
| ORD-000021 | 08:31 | 10:00 | 10:00–12:00 | 89 min |
| ORD-000041 | 10:20 | 10:20 | 10:00–12:00 | — |
| ORD-000030 | 10:34 | 12:00 | 12:00–14:00 | 85 min |
| ORD-000033 | 12:17 | 14:00 | 14:00–16:00 | 103 min |
| ORD-000038 | 14:34 | 16:00 | 16:00–18:00 | 86 min |

```
evaluate().breakdown   driving_seconds   5973
                       waiting_seconds  28326      <-- the real story
                       service_seconds   3840
                       lateness_penalty     0
verify()               legal
```

**Read the waiting.** The van spends 7 hours 52 minutes parked and 1 hour 40
minutes driving. Adding a second van would not fix that — the constraint is the
customers' slots, not the fleet. Any optimiser reporting only distance would
call this a good day and tell you nothing. `waiting_seconds` is why the
evaluator's breakdown is a dict rather than a single cost.

### 6.2 Supermarket restocking — two capacities, and only one of them binds

Kilograms and pallets at once. The interesting question is never "did it fit" but
"which dimension ran out first".

```python
Order(id=d["order_id"], kind="JOB",
      quantities={"kg": 140, "pallets": 3},          # both, on every order
      delivery=StopSpec(location_id=d["order_id"], time_windows=(DAY,),
                        service_fixed=15*60))

Vehicle(id="TRUCK-1", capacities={"kg": 1200, "pallets": 8}, shift=DAY, …)
```

Real loads across three trucks:

| Truck | Stops | Weight | Pallets | Binding |
|---|---|---|---|---|
| TRUCK-1 | 1 | 140 / 1200 | 3 / 8 | pallets |
| TRUCK-2 | 4 | 1120 / 1200 | 8 / 8 | pallets |
| TRUCK-3 | 5 | 1140 / 1200 | 8 / 8 | pallets |

**Pallets bound every truck; weight never did.** Two trucks left with their
pallet space full and 5% of their payload unused. Buying higher-payload trucks
would change nothing here — the answer is pallet configuration, and you can only
see that because both dimensions were modelled. A single-capacity model would
have reported "full" and hidden which resource to go and buy.

### 6.3 Appliance repair — only some technicians are gas-certified

```python
Order(id=job_id, kind="JOB", quantities={"jobs": 1},
      required_skills=frozenset({"GAS"}),
      delivery=StopSpec(location_id=job_id, time_windows=(DAY,), service_fixed=40*60))
```

With one uncertified technician, **before you solve anything**:

```python
from vrp.diagnose import preflight
from vrp.explain import would_fit_if

preflight(problem)
# {'ORD-000008': NO_ELIGIBLE_VEHICLE: requires ['GAS']; no vehicle carries it}

would_fit_if(problem, "ORD-000008")
# [{'change': 'vehicle_skill', 'to': 'GAS'}]
```

Certify the technician and re-run:

```python
preflight(problem)    # {} -- clear, every job servable
```

This is the pattern worth copying: **diagnose before you solve.** A solver given
this instance would have returned a plan with one order unassigned and no
statement of why. `preflight` names the order, the reason and the remedy in
milliseconds, and `would_fit_if` turns it into something a dispatcher can act on.

### 6.4 A van breaks down at 11:00

The dynamic case. What has already happened must stay happened; only the rest may
move.

```python
from vrp.triggers import Trigger, reoptimise, affected_routes
from vrp.committed import committed_prefix
from vrp.stability import churn, churn_cost

NOW  = 11 * 3600
trig = Trigger(kind="BREAKDOWN", at=NOW, vehicle_id="VAN-1", order_id=None)

{r.vehicle_id: committed_prefix(problem, r, NOW) for r in solution.routes}
# {'VAN-1': ['ORD-000008', 'ORD-000015', 'ORD-000021', 'ORD-000041']}

affected_routes(problem, solution, trig)      # {'VAN-1'}

response = reoptimise(problem, solution, trig, NOW)
```

Four stops were already delivered by 11:00 and are frozen. The re-plan moved the
remaining three:

```json
{"moved": {"ORD-000033": ["VAN-1", null],
           "ORD-000038": ["VAN-1", null],
           "ORD-000040": ["VAN-1", null]},
 "cost_before": 159814, "cost_after": 3102629,
 "lateness_before": 0, "lateness_after": 0}
```

And the disruption, priced:

```python
churn(solution, response.plan)
# {'moved': 3, 'eta_shift': 14910}

churn_cost(solution, response.plan, per_move=1000, per_second=1)
# 17910
```

**`cost_after` is 19× `cost_before` and that is the correct answer**, not a bug:
with the only van gone, three orders are now unassignable and carry the
unassigned penalty. The plan is reporting an operational fact — you need a
replacement vehicle — rather than hiding it in a cheaper-looking plan that quietly
drops the work.

**`churn` is what you tell the customer.** Three stops moved and 14,910 seconds of
ETA shift is four hours of revised promises across three customers. `churn_weight`
on `reoptimise` lets you pay to avoid that; a re-plan that saves 2% and
re-communicates twelve ETAs is usually a bad trade.

### 6.5 Hours of service — EU-561 as data, not as a duration cap

```python
from vrp.hos import EU_561, US_HOS, DriverState

EU_561
# {'name': 'EU-561', 'max_drive': 32400, 'max_duty': 46800,
#  'drive_before_break': 16200, 'break_duration': 2700,
#  'break_rule_ref': 'EC-561/2006 Art.7'}
```

Nine hours at the wheel, thirteen on duty, a 45-minute break before 4½ hours
driving — **and the article it comes from**, so a violation cites the law rather
than a magic number. A driver mid-shift is a `DriverState`, which is what lets a
plan resume a day already in progress:

```python
DriverState(drive_used=15900, duty_used=18000,
            since_last_break=15900, week_drive_used=144000)
# 15900s since the last break against drive_before_break=16200s -- still clear,
# with five minutes of driving left before a break is owed
```

### 6.6 How many vans next quarter

Tactical sizing over a generated scenario set, each mix solved on every day.

```python
from vrp.scenarios import generate_scenarios, sweep, Mix

days  = generate_scenarios(problem, days=8, seed=3)
# 8 demand days, orders per day: [4, 7, 4, 6, 3, 3, 5, 6]

mixes = [Mix(name=f"{n} vans", vehicles=tuple(van(i) for i in range(1, n+1)))
         for n in (1, 2, 3)]

results = sweep(problem, mixes, days,
                solve=lambda p: {r.vehicle_id: [s.order_id for s in r.steps if s.order_id]
                                 for r in solve(p, iterations=400, seed=0).routes})
```

| Mix | Served | Fixed | Routing | Failure | **Total** |
|---|---|---|---|---|---|
| **1 van** | 38/38 | 160,000 | 374,923 | 0 | **534,923** |
| 2 vans | 38/38 | 320,000 | 379,844 | 0 | 699,844 |
| 3 vans | 38/38 | 480,000 | 379,844 | 0 | 859,844 |

One van serves every order on every day, so the second and third are pure cost —
and the third does not even change the routing, because the second was already
idle. **`failure_cost` is the column that matters** on a real demand set: it
prices the days a mix cannot serve, which is what stops the sweep recommending a
fleet of one for an operation that occasionally needs four.

**`sweep`'s `solve` must return an assignment dict**, `{vehicle_id: [order_id]}`,
not a `Solution`. Its type is `Solve = Callable[[Problem], dict[str, list[str]]]`.

### 6.7 An operator override that cannot be honoured

Locks are hard constraints, so a contradictory set must be refused with a reason
rather than silently ignored.

```python
from vrp.locks import is_feasible_under_locks, minimal_conflict

problem = replace(problem, locks=(
    Lock(kind="PIN_ORDER_TO_VEHICLE",    order_id="ORD-000008", vehicle_id="VAN-1"),
    Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="ORD-000008", vehicle_id="VAN-1"),
    Lock(kind="FORBID_ORDER_ON_VEHICLE", order_id="ORD-000008", vehicle_id="VAN-2"),
))

is_feasible_under_locks(problem)     # False
minimal_conflict(problem)
```

The minimal conflicting set:

```json
[{"kind": "FORBID_ORDER_ON_VEHICLE", "order_id": "ORD-000008", "vehicle_id": "VAN-1"},
 {"kind": "FORBID_ORDER_ON_VEHICLE", "order_id": "ORD-000008", "vehicle_id": "VAN-2"}]
```

**Two locks, not three.** The pin is not in the conflict: with only two vehicles,
forbidding the order on both already makes it unservable whatever else is
instructed. That is an IIS-style diagnosis — the smallest set you must relax —
and it is what turns "INFEASIBLE" into a dispatcher removing one specific
instruction.

An invented lock kind is refused by name, with the full vocabulary:

```
ValidationError: unknown lock kind 'PIN_TO_VEHICLE'; §6.6 defines
FIX_ROUTE_PREFIX, FIX_SEQUENCE, FORBID_DEPLOY, FORBID_ORDER_ON_VEHICLE,
FORCE_DEPLOY, FREEZE_UNTIL, PIN_DEPOT, PIN_ORDER_TO_VEHICLE
```

### 6.8 Proving a plan came from the inputs it claims

```python
from vrp.snapshot import capture, write, read, replay, SnapshotTampered

snap = capture(problem, {"solver": "pyvrp", "seed": 0, "iterations": 3000})
path = write(snap, "grocery.snapshot.json")

again = read(path)
again.digest
# 'baaf162edba6b71a16593d69122766bb2011520cc13741659ee0b4c3208f731f'

replayed = replay(again, lambda prob, cfg: solve(prob, iterations=cfg["iterations"],
                                                 seed=cfg["seed"]))
# the replayed plan is identical to the original -- True
```

`replay` rebuilds the `Problem` **from the payload**, not from whatever happens to
be in memory. That is the only version of the test that proves anything.

Edit the file and read it back:

```
SnapshotTampered: grocery.tampered.json does not match its digest:
recorded baaf162edba6, contents hash to 43bf9be02c41. The snapshot has been
edited since it was written, so nothing derived from it can be attributed to
the plan it claims to describe
```

**The digest is checked in `read()`, at the file boundary — not in `replay()`.**
That is the right place, since it is where untrusted bytes enter, but it means an
in-memory `Snapshot` you constructed yourself is never re-hashed. If you need the
integrity guarantee, you must go through the file.

---

## 7. Module reference

### Building problems

| Module | Key names | For |
|---|---|---|
| `model` | the types in §3 | The domain itself |
| `osrm` | `build_matrix`, `matrix_version`, `Snap`, `SnapWarning` | A pinned matrix from the gateway |
| `matrix` | `build_large_matrix`, `plan_tiles`, `PairCache`, `submatrix`, `PlanarMatrix` | Tiling and pair-level caching past the gateway's cell cap |
| `generate` | `generate_instance`, `generate_large_instance`, `Shape`, `build_plan`, `plan_greedily` | Synthetic instances for property testing |
| `servicemodel` | `build`, `model_for`, `as_config`, `Resolved`, `RunConfig`, `digest` | An operation described as JSON, compiled to a `Problem` |
| `benchmarks` | `read_benchmark`, `read_solution_cost`, `gap_percent`, `Benchmark` | CVRPLIB, Solomon, Li & Lim instances |
| `bench.fixtures` | canonical instances per catalogue scenario | The P0 operational corpus |

`generate_instance(seed=1)` really returns `6 orders, 4 vehicles, 7 locations,
matrix generated-v1` — a self-contained instance with no gateway needed.

### Solving

| Module | Key names |
|---|---|
| `solve.pyvrp_adapter` | `solve(problem, iterations=500, seed=0)`, `compile_problem`, `map_solution` |
| `solve.ortools_adapter` | `solve(problem, solutions=200, seed=0)` |
| `lns` | `lns_search`, `sisr_ruin`, `random_ruin`, `greedy_recreate`, `Acceptance` |
| `localsearch` | `accelerated_search`, `naive_search` |
| `polish` | `polish_route`, `tsptw_sequence`, `optimal_departure`, `duty_duration` |
| `setpartition` | `build_pool`, `select_routes`, `RoutePool`, `PooledRoute` |
| `decompose` | `partition`, `partition_spatially`, `popmusic`, `repair_boundaries`, `SubProblem` |
| `portfolio` | `run_portfolio`, `Portfolio`, `Outcome`, `instance_signature`, `WinRates` |
| `fleet` | `minimise_fleet`, `routes_needed`, `Ejection` |
| `accelerate` | `accelerated_portfolio`, `accelerator_available`, `cuopt_engine` |

### Judging

| Module | Key names |
|---|---|
| `evaluator` | `evaluate`, `build_timeline`, `route_metrics`, `route_is_legal`, `soft_penalties`, `lateness`, `Evaluation`, `ObjectiveWeights`, `Attainment` |
| `verify` | `verify`, `Report`, `Violation`, `NOT_APPLICABLE` |
| `objective` | `score`, `compare`, `tier_scales`, `ObjectiveSpec`, `Mode`, `Tier`, `Score`, `TierValues`, `Rates` |
| `modelcheck` | `check`, `passes`, `compare`, `binding_constraint`, `structural`, `ModelResult`, `Binding` |

### Constraints

| Module | Key names |
|---|---|
| `hos` | `EU_561`, `US_HOS`, `HoursOfServiceRules`, `DriverState`, `Activity`, `Break`, `DutyRecord`, `Placement`, `read_duty` |
| `timedependent` | `travel`, `arrival`, `SpeedProfile`, `ArcKey`, `arc_class_of`, `fastest_possible`, `filter_moves` |
| `electric` / `battery` | `plan_charging`, `NoChargerReachable` / `ChargingCurve`, `ChargeStop`, `charge_seconds`, `energy_wh`, `consumed_ppt` |
| `locks` | `is_feasible_under_locks`, `minimal_conflict` |
| `synchronise` | `solve_synchronised`, `unmet` |
| `depots` | `solve_within_inventory`, `drawn_per_depot`, `over_drawn` |

`EU_561` is a real rule set, not a duration cap:

```
{'name': 'EU-561', 'max_drive': 32400, 'max_duty': 46800,
 'drive_before_break': 16200, 'break_duration': 2700,
 'break_rule_ref': 'EC-561/2006 Art.7'}
```

Nine hours driving, thirteen on duty, a 45-minute break before 4½ hours at the
wheel — each carrying the article it comes from, so a violation cites the law.

### Dynamic operation

| Module | Key names |
|---|---|
| `epochs` | `epochs`, `classify`, `must_go`, `decide`, `Epoch`, `Dispatch`, `Classification` |
| `policies` | `greedy`, `lazy`, `random_policy` |
| `pcdispatch` | `pc_policy`, `epoch_problem`, `tune` |
| `icd` | `icd_policy`, `Thresholds` |
| `committed` | `committed_prefix`, `commit_locks`, `loading_locks`, `moved_since`, `moved_between`, `Execution` |
| `triggers` | `reoptimise`, `Trigger`, `Response`, `Delta`, `affected_routes`, `preempt`, `recover_from_absence` |
| `stability` | `churn`, `churn_cost`, `tradeoff`, `Churn`, `Point` |
| `replay` | `replay`, `generate_days`, `compare`, `Day`, `Run`, `PolicyResult`, `EpochRecord` |
| `jobs` | `SolveService`, `JobHandle`, `JobStatus`, `RegisteredProblem` |

The breakdown-at-eleven shape:

```python
from vrp.triggers import Trigger, reoptimise        # Trigger(kind, at, vehicle_id, order_id)
from vrp.committed import Execution, committed_prefix

response = reoptimise(problem, plan, trigger, now,
                      neighbours=1, solve=None, churn_weight=0)
```

`committed_prefix(problem, route, now, include_en_route=False, execution=None)`
gives what may never be replanned; `Execution(completed, en_route)` is what
actually happened. `churn_weight` prices the disruption — raise it and the
re-plan protects ETAs you already communicated.

### Fleet and allocation

| Module | Key names |
|---|---|
| `allocate` | `allocate`, `marginal_values`, `AllocationReport`, `VehicleAllocation` |
| `scenarios` | `recommend`, `sweep`, `pareto`, `average_day`, `recovery_cost`, `Scenario`, `Mix`, `MixResult` |
| `consistency` | `territories`, `consistency_price`, `imbalance`, `arrival_spread`, `distinct_drivers`, `align_departures`, `Horizon`, `Price`, `Spread` |
| `periodic` | `schedule`, `compliance`, `eligible_days`, `Recurrence`, `Compliance` |
| `zones` | `learn_prior`, `order_by_prior`, `zone_of`, `ZonePrior` |

### Learning from execution

| Module | Key names |
|---|---|
| `adherence` | `ingest`, `adherence`, `aggregate`, `dissimilarity`, `ExecutedRoute`, `Adherence` |
| `calibrate` | `fit`, `drift`, `as_service_fixed`, `archetype_of`, `observations`, `Calibration`, `Observation`, `Archetype` |
| `speedfit` | `fit`, `recalibrate`, `validate`, `traversals`, `SpeedCalibration`, `Traversal`, `Validation` |

### Operations and provenance

| Module | Key names |
|---|---|
| `snapshot` | `capture`, `write`, `read`, `replay`, `canonical`, `Snapshot`, `SnapshotTampered` |
| `observe` | `Recorder`, `RunRecord`, `Incumbent` |
| `rollout` | `shadow`, `decide`, `divergences`, `Canary`, `ShadowDay`, `Decision`, `Criterion`, `Failure` |
| `anonymise` | `anonymise`, `write_corpus`, `identifying`, `metres_between`, `Anonymised` |
| `diagnose` | `preflight`, `Finding` |
| `explain` | `explain`, `explain_assignment`, `would_fit_if`, `without`, `Explanation`, `Rationale`, `Change`, `Rejection` |
| `quote` | `quote_insertion`, `quote_removal`, `Quote`, `NoRoomForOrder` |

`capture(problem, config) -> Snapshot(payload, digest)`. A real digest:

```
9bf8a41369cc90fb6ddc9f3c8db0de8b475092265b1504196ea89c8502c5aa93
```

`SnapshotTampered` is raised on read when the payload no longer matches its
digest — a plan is replayable from its snapshot or it is not a plan.

---

## 8. The one public HTTP contract — `vrp.api`

`/verify` is the single endpoint in the specification's API surface that needs no
solver: a pure function from (problem, plan) to a report. `vrp.api` implements
its request and response shapes, "ready for whichever process ends up hosting
it".

```python
from vrp.api import verify_request, as_json, VerificationError

report = verify_request(payload)     # dict in, dict out
```

Real output, verifying a plan that serves nothing:

```json
{"ok": false, "checked_by": "verifier@1.0.0",
 "hard_violations": [
   {"invariant": "INV-1", "detail": "neither served nor listed unassigned",
    "vehicle_id": null, "order_id": "ORD-000008"},
   {"invariant": "INV-1", "detail": "neither served nor listed unassigned",
    "vehicle_id": null, "order_id": "ORD-000015"}],
 "soft_violations": [],
 "invariants_passed": ["INV-10", "INV-11", "INV-12", "INV-2", "INV-3", …]}
```

**The parser refuses rather than helps.** It will not infer a missing arrival,
coerce a stringy number, or default an absent window. A stringy `service_fixed`:

```
VerificationError: order ORD-000008 delivery 'service_fixed' must be an integer, got '300'
```

Being helpful would produce a report about a plan the integrator did not send —
and it would pass, which is worse than failing.

---

## 9. Exceptions

| Exception | Module | Raised when |
|---|---|---|
| `ValidationError` | `model` | A domain object is constructed in a state the specification forbids |
| `UnreachableArc` | `model` | Travel is read for a pair no route connects |
| `SnapWarning` | `osrm` | A location snapped further than the threshold — a **warning**, not an exception |
| `NoRoomForOrder` | `quote` | No vehicle can legally take the order |
| `NoChargerReachable` | `electric` | An EV round cannot be rescued by any charge |
| `VerificationError` | `api` | A `/verify` payload is not readable |
| `SnapshotTampered` | `snapshot` | A snapshot's payload no longer matches its digest |
| `UnsendableEngine` | `portfolio` | An engine cannot be dispatched to a worker process |
| `NotImplementedError` | `solve.pyvrp_adapter` | An instance contains an unreachable arc. The adapter cannot express a forbidden one, so it is named rather than approximated — see §10 |
| `NotImplementedError` | `solve.pyvrp_adapter` | `tier_bonuses` would need a prize bonus past int64 — roughly 50–55 distinct `priority_tier` values. Refused rather than wrapped, because a wrapped bonus inverts the ranking silently on an instance that looks ordinary. Ranks are classes; a score belongs in `Order.prize`, which has no such ceiling |

---

## 10. Things that cost me time

Written down because I hit every one of them writing this document against the
real package.

| Expectation | Reality |
|---|---|
| `import vrp` gives you the API | `__init__.py` exports nothing. Import the module you want |
| `from vrp.solve import solve` | `vrp.solve` is a package with no re-exports — `from vrp.solve.pyvrp_adapter import solve` |
| `kind="DELIVERY"` | `JOB` or `SHIPMENT` only. A `JOB` takes exactly one of `pickup`/`delivery` |
| `preflight()` returns a list | Returns `dict[str, Finding]`. Servable orders are absent |
| `explain(problem, assignment, order_id)` | `explain(problem, solution)`; per-order is `explain_assignment(problem, solution, order_id)` |
| `quote_insertion(problem, solution, order)` | `(problem, assignment_dict, order_id)` — the order must already be in the `Problem` |
| `evaluate(problem, solution)` | Takes an assignment dict, not a `Solution` |
| `Evaluation.feasible` | `Evaluation` is `(total, breakdown, timelines)`. Feasibility comes from `verify` |
| `report.ok` | `Report` is `(violations, not_applicable)`. Use `bool(report)` |
| `score().values` is a dict | It is a `TierValues` wrapping `.values` — index it by `Tier` |
| `build_matrix(..., [(lon, lat)])` | `(latitude, longitude)`, the opposite of the JSON bodies |
| An unreachable arc is merely expensive | It is `-1`, the **cheapest** value in the matrix. Before `v0.3.3` the solver was drawn to the stranded stop and visited it *first*; it now raises. The gateway's sentinel is `1e12` and fails the opposite way — admissible rather than attractive |
| `vrp/bench` is importable anywhere | Checkout only. It is excluded from the wheel, so an installed copy has no `vrp.bench` |
| `Lock(kind="PIN_TO_VEHICLE")` | `PIN_ORDER_TO_VEHICLE`. The error lists all eight valid kinds |
| `sweep(..., solve=...)` takes your solver | It must return an **assignment dict**, not a `Solution` — `Solve = Callable[[Problem], dict[str, list[str]]]` |
| `MixResult.mix` is a `Mix` | It is the mix's **name**, a `str`. Totals are on `.total` |
| `replay()` checks the digest | `read()` checks it, at the file boundary. An in-memory `Snapshot` is never re-hashed |
| `Scenario.order_ids` | `Scenario.orders` — real `Order` objects, not ids |

---

### An unreachable arc is refused, not priced

`vrp/model.py` sets `UNREACHABLE = -1`. In a minimisation that is not "outside
the range of any real cost" — it is the most attractive value in the matrix.

The adapter has always omitted unreachable edges when compiling, citing MTX-5
while doing it. **Omitting is not forbidding.** PyVRP routes through a missing
edge anyway and returns the plan marked `INFEASIBLE`, so the comment asserted a
guarantee the library does not give. A caller who checked `status` was safe; one
who counted stops got a route that visited the stranded stop first.

Since `v0.3.3` the adapter refuses before compiling:

```python
solve(problem)          # an instance with a severed pair
# NotImplementedError: 2 of this instance's arcs are unreachable, the first
# from O2 to O3, and this adapter cannot express a forbidden arc …
```

It does **not** substitute a large number of its own — MTX-5 forbids that too,
because a large-finite sentinel gets optimised back into the solution. For
*which* order is stranded rather than that one is, `diagnose.preflight` answers
first; this is the backstop for a caller who did not ask.

**This is a break, not only an improvement.** An instance with an unreachable
pair used to return a plan with `INFEASIBLE`. It now raises, so anything
inspecting or counting that plan gets an exception instead.

Two shapes, both refused — the second is the one real road data produced, two
delivery points reachable from their facility but not from each other:

```
island cut off from the depot too                    -> refused, 6 arcs
two stops reachable from depot, not from each other  -> refused, 2 arcs
```

## 11. Reproducing this

```bash
# engine and gateway, for build_matrix
osrm-routed --algorithm mld cr.osrm --port 5000 --max-table-size 100 &
OSRM_BASE_URL=http://127.0.0.1:5000 HOST=127.0.0.1 PORT=8000 \
  ./gateway/target/debug/osrm-api-gateway &

# the library
uv pip install -e ".[dev]"
uv run python your_script.py
```

`vrp.generate.generate_instance(seed=1)` needs no gateway at all if you only want
to exercise the API.

### Where to look next

| For | Read |
|---|---|
| What the VRP is and does | [`40-what-the-vrp-does.md`](40-what-the-vrp-does.md) |
| The HTTP endpoints | [`42-endpoint-guide.md`](42-endpoint-guide.md) |
| The specification behind all of it | [`../vrp-spec-driven-development.md`](../vrp-spec-driven-development.md) |
| Worked examples per capability | `examples/src/fleet/` — see the index in the VRP guide |
| Measured quality and the verifier under mutation | [`../whitepapers/03-feasibility-is-a-gate.md`](../whitepapers/03-feasibility-is-a-gate.md) |

---

*Captured 2026-09-13 against `vrp/` at the current working tree, with a live
gateway on a Costa Rica graph. Signatures are from introspection; outputs are
real return values; exception texts are real. `pyvrp:0.14.0`. Counts and the
packaging section re-checked 2026-09-16 against `vrp-platform` 0.3.1.*
