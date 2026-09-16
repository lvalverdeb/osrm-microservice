# 40 — What the VRP actually does

*Tier 2: authored from `gateway/src/vrp/`, `vrp/`, `docs/vrp-spec-driven-development.md`
and the whitepaper experiments. Field lists and counts are generated in
[tier 1 doc 08](../tier1/08-module-map.md).*

A reference for anyone who has to explain, integrate with, sell, operate or
extend the vehicle-routing capability in this repository.

> **Versión en español:** [QUE_HACE_EL_VRP.md](../vrp/QUE_HACE_EL_VRP.md). Both are
> maintained in parallel; where they diverge, this one is the copy written
> against the code.

**If you do not already know what a vehicle routing problem is, start at
[Part 0](#part-0----what-a-vehicle-routing-problem-is).** It explains the problem
itself in plain language, with real operations as examples, and assumes nothing.
The rest of this document assumes it.

**Then read this: two different things in here are called "the VRP", and they
are not versions of each other.** Almost every confusion about what this system
does traces back to conflating them.

| | `POST /vrp` on the gateway | The `vrp/` Python platform |
|---|---|---|
| What it is | Two HTTP endpoints on the Rust gateway | A domain model, evaluator, verifier and solver stack |
| Where | `gateway/src/vrp/` -- 1,034 lines of Rust | [`vrp/`](../tier1/08-module-map.md) -- a Python library |
| Deployed? | **Yes.** It is in front of traffic today | **No.** A library; no process hosts it |
| What it optimises | Which depot serves a stop, and the order one vehicle drives its stops | Everything below: windows, shifts, skills, hours, loads, prizes, charge |
| Understands time? | No. No windows, no service duration, no clock | Yes. Windows, service time, shifts, driving hours, time-dependent travel |
| Understands load? | No. `capacity` means *stops per vehicle* | Yes. Multi-dimensional quantities against per-vehicle capacities |
| Solver | OSRM's `/trip` (a TSP heuristic) | PyVRP and OR-Tools adapters, a custom LNS, set-partitioning polish, decomposition |
| Answer quality | 8.2%--14.8% worse than a real solver, measured | The reference the gateway was measured against |
| Status | Production | 84 of 86 backlog tasks done; 2 blocked, neither on effort |

If someone asks "does your VRP handle delivery time windows?", the answer is
**the shipped endpoint does not, and the library does.** Everything else in this
document elaborates that sentence.

---

# Part 0 -- What a vehicle routing problem is

*Skip this part if you already work with routing software. It is here so that a
dispatcher, a finance lead or a new joiner can read the rest.*

## 0.1 The problem, in one paragraph

You run a warehouse. This morning it holds 600 parcels that have to reach 600
different addresses today, and you have six vans and six drivers. **Which
parcels go on which van, and in what order does each driver drive them?**

That is a vehicle routing problem. Everything else -- capacities, delivery
windows, driver shifts, skills -- is detail layered on those two questions. A
piece of routing software is a machine for answering them.

The name is literal and about as old as commercial computing: Dantzig and
Ramser posed it in 1959 as "the truck dispatching problem". The questions have
not changed. The fleets got bigger.

## 0.2 It is not the same question a map app answers

This is the single most common misunderstanding, and it is worth being precise
about.

| | A map app | A vehicle routing problem |
|---|---|---|
| Question | How do I get from **A to B**? | Given **600 Bs**, six vans and one warehouse, who takes which, in what order? |
| Answer | One route | An assignment *and* an order for every vehicle |
| Choices to weigh | Which roads | Which van, which position in that van's day, which roads |
| Typical use | A driver, at the moment of driving | A planner, the night before |

A map app is a component *inside* a routing system -- it is what tells you the
drive from stop 12 to stop 13 takes nine minutes. It does not decide that stop
13 should follow stop 12, or that they belong to the same driver at all.

In this repository that division is literal: **OSRM is the map engine** and
answers "how far, how long, by what roads". **The VRP is the layer that decides
who goes where.**

## 0.3 Why it is hard: the numbers get silly very fast

Give one van ten stops and there are 362,880 possible orders to drive them in.
Twenty stops is 121,645,100,408,832,000 -- over a hundred quadrillion. Twenty
five stops is a number with 24 digits in it.

And that is the *easy* half, because it assumes you already know which stops
that van is taking. Splitting 60 stops across six vans can be done in about
5 x 10^46 ways before anyone has ordered a single one of them.

Three consequences follow, and they explain most of what routing software does:

1. **Nobody finds the best answer.** Not this system, not the expensive ones.
   Checking every possibility is not slow, it is physically impossible. Every
   real system finds a *good* answer within a time budget and stops.
2. **So "how good?" is a real question with a real number**, which is why
   §6 of this document measures the gateway's answers against a stronger solver
   rather than asserting they are fine. A routing vendor who will not give you
   that number has not measured it.
3. **Small changes to the question change the answer a lot.** "Use the fewest
   vans" and "drive the fewest kilometres" are different problems with different
   answers, and a system has to be told which one you meant. That is what §9's
   objective modes are.

## 0.4 What makes the answer look wrong at first

Plans produced by routing software routinely offend the intuition of people who
know the territory. Usually the software is right, and there are two measured
reasons why.

**The nearest customer on a map is often not the nearest to drive to.** In this
project's own Costa Rica data, a straight line understates the real drive by
about **40% at the median**. In the worst pair sampled, two addresses **914
metres apart** were **16.1 km apart by road** -- a river, a motorway and a
one-way system in between. A dispatcher looking at pins on a map is reading
straight lines. The planner is reading roads.

**Getting there and coming back are not the same journey.** Two-thirds of
address pairs in the metropolitan sample have a different distance out than
back, because of one-way streets and turn restrictions. A route that looks
wasteful in one direction may be the cheap direction.

There is a third reason that is not about geography at all: **the same stops can
make a very different day depending on how they are grouped.** Six real San José
stops in two tight clusters cost **95.03 km** when they were handed over
alternating between the clusters, and **49.68 km** when they were handed over
grouped. Identical stops, identical depot -- nearly double the driving. Grouping
is a decision, and it is one the software has to get right.

## 0.5 The words people use

| Word | Means |
|---|---|
| **Depot** | Where vehicles start, and usually finish. A warehouse, a yard, a pharmacy |
| **Stop** / **order** / **job** | One place something has to happen. A delivery, a collection, a repair visit |
| **Route** | One vehicle's whole day: depot, stops in order, back to depot |
| **Fleet** | The vehicles available, which may differ from each other |
| **Capacity** | What a vehicle can carry. Kilograms, pallets, crates, seats -- often several at once |
| **Time window** | When a stop may be served. "Between 9 and 12", "after the shop opens" |
| **Service time** | How long the stop itself takes, separate from driving to it |
| **Shift** | When the driver may work, including breaks and legal driving limits |
| **Feasible** | A plan that breaks no hard rule. The first thing to get right |
| **Optimal** | The cheapest possible plan. Nobody has one; the honest goal is *good, and provably feasible* |

The distinction in the last two rows is the backbone of this system's design:
**feasibility is a gate and optimality is a target.** A plan that saves 8% and
sends a driver to a customer who is shut is not an 8% saving, it is a failed
delivery and a phone call.

## 0.6 What routing looks like in the real world

Each row is a real operation type, and the middle column is what makes it hard
-- which is what decides whether the shipped `/vrp` endpoint can serve it or
whether you need the platform in Part II.

| Operation | What binds it | Served by |
|---|---|---|
| **Parcel delivery** -- courier, 600 drops from one hub | Sheer number of stops; vans fill up | The gateway's `/vrp` handles this |
| **Supermarket restocking** -- pallets from a distribution centre to stores | Weight *and* pallet count at once; loading bay slots | Platform -- multi-dimensional capacity |
| **Appliance repair** -- technicians visiting homes | The customer's appointment window; the job takes 40 minutes; only some techs are gas-certified | Platform -- windows, service time, skills |
| **Waste collection** -- a hopper that fills up | The truck must empty at the tip mid-shift and come back | Platform -- multi-trip and reloading |
| **Home healthcare** -- nurses visiting patients | Visit windows, qualifications, and the same nurse seeing the same patient | Platform -- windows, skills, consistency |
| **School transport** | Seats, and how long a child may be aboard | Platform -- capacity and ride-time limits |
| **Fuel or LPG distribution** | Compartments, axle weights, site access restrictions | Platform -- compartments, access classes |
| **Field sales / merchandising** | Each store visited twice a week, not just once this week | Platform -- multi-period horizon |
| **Same-day courier** | Jobs arrive *during* the day, after the plan was made | Platform -- dispatch waves, re-optimisation |
| **Grocery e-commerce** | The customer chose a two-hour slot at checkout | Platform -- hard time windows |
| **Signed-document courier** | Ten minutes standing at every door; the riding barely matters | Platform -- service time dominates |

Read that column and the shape of this repository becomes clear: **the shipped
endpoint serves the first row well and the rest not at all**, which is exactly
why the platform exists.

## 0.7 What a planner actually has to decide

Before any software can help, someone has to answer three business questions.
They are not technical, and getting them wrong is the usual cause of a plan
nobody trusts.

1. **What must never happen?** A missed appointment window? An overloaded van?
   A driver over their hours? These become *hard constraints*, and a plan that
   breaks one is thrown away no matter how cheap it is.
2. **What are we trying to minimise?** Kilometres, hours, vehicles, cost, or
   lateness. These pull against each other -- the plan with the fewest vans is
   almost never the plan with the fewest kilometres.
3. **What would we prefer, all else equal?** Balanced workloads, the same driver
   on the same street daily, compact territories. These are real value and they
   are the first thing to sacrifice when the day is tight.

This system encodes that ordering literally: hard rules first, the thing you are
minimising second, preferences last, and no amount of the third ever buys any of
the first. §9 is that structure written out.


---

# Part I -- The gateway's `/vrp`

## 1. What it is for

You have some depots and a pile of stops. You want to know which depot serves
which stop, and what order each vehicle should drive. You do not need to model
when the customer is home, how long the drop takes, or what the parcels weigh.

That is the whole product. It is a *territory-and-sequence* service, and within
those bounds it is fast, cached, bounded and honest about refusing what it
cannot do.

## 2. The request, field by field

```json
{
  "depots": [{"id": "D1", "longitude": -84.09, "latitude": 9.93}],
  "stops":  [{"id": "S1", "longitude": -84.10, "latitude": 9.94},
             {"id": "S2", "longitude": -84.14, "latitude": 9.96}],
  "capacity": 35,
  "clustering_mode": "travel_time",
  "hysteresis_m": 2000.0,
  "max_radius_km": 25,
  "roundtrip": true
}
```

| Field | Default | What it **actually** does |
|---|---|---|
| `depots` | required, 1--500 | Origins. Each may carry an `id`, which becomes the vehicle label |
| `stops` | required, 1..`VRP_MAX_STOPS` (2000) | The work. Each may carry an `id`, echoed back in the route |
| `capacity` | 35 | **Maximum stops on one vehicle.** Not weight, not volume, not units. It is the chunk size |
| `vehicle_count` | none | **Never read by the solver.** It is validated (`> 0`) purely so an old client sending `0` still gets its 422 |
| `clustering_mode` | `travel_time` | `travel_time`, `distance` or `radial` -- which cost decides the depot |
| `hysteresis_m` | 2000.0 | How much better a non-obvious depot must be before a stop moves to it |
| `max_radius_km` | none | Beyond this road distance from its depot, a stop is reported unreachable rather than served |
| `roundtrip` | true | Whether the vehicle returns to the depot |

Two of those rows surprise almost everyone. **`capacity` is a stop count**, so
`"capacity": 35` means "no vehicle gets more than 35 drops" and says nothing
about what is in the van. **`vehicle_count` does nothing at all** -- the number
of vehicles is an *output*, derived from how many chunks the stops divide into.

## 3. The algorithm, in four phases

```
  stops + depots
        |
   [1]  |  POST /table  ->  depots x stops duration and distance matrices
        v
   [2]  allocate: pick one depot per stop        (gateway/src/vrp/allocate.rs)
        |    anchor -> sanity -> unreachable -> hysteresis
        v
   [3]  sweep + chunk: order by bearing, slice into vehicle loads
        |                                          (gateway/src/vrp/solve.rs)
        v
   [4]  per chunk: POST /trip  ->  OSRM solves that vehicle's TSP
        |    concurrency bounded by VRP_CHUNK_CONCURRENCY
        v
  routes + totals
```

### Phase 1 -- the cost matrix

One `/table` call gives a depots x stops matrix of durations and distances.
OSRM reports an unroutable pair as `null`; the gateway substitutes a sentinel
of `1e12` so the comparisons below have a number to work with. A pair carrying
the sentinel is a pair with no road connection, and that is tracked, not hidden.

### Phase 2 -- allocation: which depot serves this stop

This is the part with actual opinions in it. For each stop, in order:

1. **Anchor.** The nearest depot as the crow flies. Equirectangular distance
   with one longitude scale taken from the centroid of all stops -- computed
   once per request, not per stop.
2. **Radial mode stops here.** It never consults the road matrix at all.
3. **Best.** The cheapest depot in the chosen matrix -- durations for
   `travel_time`, distances for `distance`.
4. **Visual sanity.** If `best` is more than `VRP_SANITY_LIMIT_M` (50 km)
   *further away in a straight line* than the anchor, take the anchor anyway.
   This exists to stop a single fast highway making a distant depot look
   "better" in a way no dispatcher would accept.
5. **Unreachable handling.** If `best` carries the sentinel, take the anchor.
   If the anchor does, take `best`.
6. **Hysteresis.** Move off the anchor only if `best` beats it by more than the
   band: `best_cost < anchor_cost - hysteresis`. Otherwise stay on the anchor.

The stop is then rejected as unreachable if the road distance from its chosen
depot carries the sentinel (nothing can route to it) or exceeds
`max_radius_km`. Everything else is assigned.

> **The order of those checks is the algorithm.** Changing it changes results.
> Ties go to the lowest depot index, which is what makes the same input produce
> the same plan every time.

**What hysteresis is worth, measured.** Its shipped default holds **2 of 400
stops** at national depot spacing and **17 of 400** at urban spacing
(`docs/whitepapers/02` §4). Its effect is a function of depot geometry, not of
the number. Do not tune it in the abstract -- measure it on your own depots.

> A correction worth carrying: `SDD.md` §3.6 describes hysteresis as keeping
> territories stable *between runs*. It cannot. There is no previous assignment
> in a `/vrp` request; the band compares an air-distance anchor against the
> road-cost best **within a single run**.

### Phase 3 -- sweep order and chunking

A depot's stops are sorted by **bearing** around the depot, longitude scaled by
`cos(latitude)` so a bearing is a bearing rather than one stretched by the
projection. Chunks are then contiguous slices of that order, so each vehicle
gets a wedge of territory rather than an arbitrary slice of the input.

This is not cosmetic. Six stops in two tight clusters cost **95.03 km submitted
alternating and 49.68 km submitted grouped** -- the same stops, the same depot,
the only difference being the order they arrived in. Sorting by bearing makes
the answer depend on where the stops are.

The sweep then **rotates to start after the widest empty wedge**. The `atan2`
cut at +/-pi is an artifact of the coordinate system, not of the stops: a
cluster lying due west of the depot has members on both sides of it, and
slicing there splits that cluster across two vehicles -- exactly what the sweep
exists to prevent. Six San Jose stops stayed at 94.56 km until the cut moved,
against 49.68 km once it did.

Chunk size is `min(VRP_CHUNK_SIZE, capacity, 199)`, floored at 1. The 199 is
OSRM's `/trip` limit of 200 coordinates minus the depot.

**One chunk is never swept.** A single chunk has no membership to decide, and
reordering it would change the upstream `/trip` URL, its cache key and its
parity fixture, for no change to the route.

### Phase 4 -- the per-vehicle TSP

Each chunk becomes one `/trip` call of depot-plus-stops. **The gateway does not
solve the TSP.** OSRM does, and the gateway maps the optimised waypoint order
back onto the caller's stop indices, drops waypoint 0 (the depot), and relays
the geometry bytes unchanged.

Chunks run concurrently, bounded by `VRP_CHUNK_CONCURRENCY` (default 4), so one
solve cannot saturate the engine. The first failure aborts its siblings rather
than leaving them running for a response nobody will read.

## 4. The response

```json
{
  "code": "Ok",
  "routes": [{
    "vehicle_id": "D1-1",
    "depot_index": 0,
    "stops_indices": [0, 1],
    "stop_ids": ["S1", "S2"],
    "stop_coordinates": [...],
    "route_geometry": {"type": "LineString", "coordinates": [...]},
    "distance_meters": 12450.0,
    "duration_seconds": 920.0
  }],
  "total_distance": 12450.0,
  "total_duration": 920.0
}
```

**Vehicle labels.** A depot with an `id` that needs one vehicle is labelled with
that id. One that needs several gets `<id>-1`, `<id>-2`. A depot with no id
falls back to a running integer across the whole response.

**`/vrp` carries no `unreachable_stops` field.** That is a deliberate
compatibility choice -- the FastAPI predecessor's response model stripped it, so
callers never saw it. Use **`POST /vrp/allocate`** to see them: the same request
body, returning depot-to-stops assignments and the unreachable list, without
running any TSP. It is the right endpoint for "check the territories look sane
before committing to a plan".

## 5. What it does not do

Stated plainly, because every one of these is a question someone will ask:

| Not supported | Consequence |
|---|---|
| Delivery time windows | A plan cannot know the customer is out between 12 and 2 |
| Service duration per stop | A ten-minute signature and a kerbside drop cost the same: nothing |
| Driver shifts, breaks, hours of service | Route length is bounded by stop count, not by a working day |
| Load, weight, volume, compartments | `capacity` counts stops; a pallet and an envelope are identical |
| Vehicle skills, access classes, incompatibility | Any vehicle may serve any stop |
| Time-of-day traffic | Every plan assumes free-flow speed. There is no departure time |
| Priorities, prizes, droppable orders | Every stop is served or reported unreachable; nothing is *chosen* against |
| Pickups paired with deliveries | Stops are independent; there is no precedence |
| Heterogeneous fleets | One chunk size for everyone |
| Multi-trip / reloading | A vehicle makes one trip |

Also worth saying: **there is no fleet-count optimisation.** The number of
vehicles falls out of `ceil(stops_at_depot / capacity)`. If you want the
cheapest fleet, that is a different question and a different tool.

## 6. How good are the answers?

Measured against PyVRP on the identical instance and fleet -- 60 GAM stops, one
depot, 2,000 iterations, seed 0 (`docs/whitepapers/03` §3):

| Fleet | Gateway | PyVRP | Gap |
|---|---|---|---|
| 1 vehicle (cap 60) | 341,105 m | 315,173 m | **+8.2%** |
| 3 vehicles | -- | -- | **+11.7%** |
| 6 vehicles (cap 10) | 481,715 m | 419,593 m | **+14.8%** |

Two readings matter. **The single-vehicle gap isolates sequencing** -- with one
vehicle there is no partition to get wrong, so +8.2% is what OSRM's `/trip`
gives up against a real solver on pure ordering. **The gap grows with the
fleet**, because with six vehicles the sweep-and-chunk partition is also being
judged, and it is a heuristic partition.

And the quiet result: the gateway's reported `total_distance` and the Python
canonical evaluator, recomputing from the same matrix, **agree to within 0.9 m
on plans of 341--482 km**. That is decimal rounding. The gateway's arithmetic is
not where the 14.8% goes.

**Compute is the other half of the trade.** On the same instance with three
vehicles, PyVRP reaches −10.5% at 3,200 iterations in 560.9 ms -- but at a
comparable wall-clock of about 12 ms it is *already 7% ahead*. The gateway's
speed advantage is smaller than it looks.

## 7. Operational limits

| Limit | Default | Behaviour when crossed |
|---|---|---|
| `VRP_MAX_STOPS` | 2000 | `422` naming the limit |
| `VRP_MAX_CONCURRENCY` | 1 per worker | Queue, then `503` with `Retry-After` after `VRP_QUEUE_TIMEOUT` |
| `VRP_MAX_QUEUE_DEPTH` | 0 (disabled) | Reject immediately instead of making the caller wait to be refused |
| `VRP_CHUNK_SIZE` | 80 | Caps stops per vehicle regardless of `capacity` |
| `VRP_CHUNK_CONCURRENCY` | 4 | Concurrent `/trip` calls inside one solve |
| `MATRIX_MAX_CELLS` | 10000 | The depots x stops matrix must fit |
| Rate limit | `100/minute` | `429` |

**Peak memory is stops x concurrent solves.** One 2,000-stop solve peaked at
277 MB; four concurrent reached 615 MB on a 2 GB host. Node-wide concurrency is
`workers x VRP_MAX_CONCURRENCY` -- raise the two together, against a measured
ceiling.

---

# Part II -- The `vrp/` platform

Everything Part I said the gateway cannot do, this can. It is a Python library
built to `docs/vrp-spec-driven-development.md`, a specification with
a constitution, a requirements catalogue, an invariant set and an ordered
backlog. **84 of its 86 tasks are done**, and the two that are not are blocked on
the outside world rather than on effort: `T-84` (compiling a problem into NVIDIA
cuOpt) waits on a machine with a card in it, and `T-99` (production `P1`/`P2`
fixtures) waits on a live operation to supply delivery records.

It is a library. Nothing hosts it as a service. `vrp/api.py` implements the
`/verify` request and response contract precisely because that one endpoint
needs no solver -- it is a pure function from (problem, plan) to a report --
"ready for whichever process ends up hosting it".

## 8. The domain model

Solver-independent by construction: nothing in `vrp/model.py` knows how a route
is produced, only what a legal one looks like.

| Entity | Carries |
|---|---|
| `Location` | id, lat/lon, matrix index, dwell overhead, dock capacity, inventory, access classes, max vehicle weight |
| `TimeWindow` | start, end, hardness (`HARD`/`SOFT`), earliness and lateness cost per second |
| `StopSpec` | one end of an order: location, **multiple** time windows, fixed service time, per-unit service time |
| `Order` | id, kind, multi-dimensional quantities, pickup and/or delivery `StopSpec`, priority tier, prize, release time, required skills, max ride time, order class, incompatibilities |
| `Vehicle` | capacities **per dimension**, shift window, start and end locations, max duration and distance, skills, fixed cost, cost per metre/second/order, overtime rate, profile, service factor, reload locations and limits, battery and charging curve, access class, gross weight, open-route flag, hours-of-service rules, initial driver state |
| `TravelMatrix` | pinned durations and distances, a `version` hash, and a `degraded` marker |
| `Lock` | an operator instruction the plan must honour exactly |
| `Synchronisation` | two routes required to meet, with min and max gap |
| `Problem` / `Solution` | the whole instance, and a plan over it |

Note what is plural: **capacities** is a dict of dimensions, so kilograms and
pallets and crates are all constrained at once. **Time windows** is a tuple, so
"9--12 or 14--17" is one order, not two. Everything is integers -- money in
minor units, time in seconds, distance in metres -- because floating-point
accumulation across a 200-stop timeline is how you get an evaluator and a
verifier that disagree.

## 9. The objective is a hierarchy, not a weighted sum

The specification is blunt about why: naive weighted sums are "the most common
modelling error in production routing", because weights that balance on a
200-stop day silently invert on a 2,000-stop day.

```
Tier 0  Hard-constraint violations       (zero in a FEASIBLE solution)
Tier 1  Unserved priority-0 orders
Tier 2  Unserved orders by descending priority tier
Tier 3  Fleet cost: fixed cost of deployed vehicles
Tier 4  Operating cost: distance + duration + overtime
Tier 5  Soft violations: earliness, lateness, soft capacity
Tier 6  Tie-breakers: workload balance, consistency, compactness
```

Tier weights are **derived from the instance**, not hard-coded, each chosen to
strictly dominate the maximum attainable value of every tier below it.

Five modes change *which tiers share a level*, never their order:

| Mode | Levels | For |
|---|---|---|
| `MIN_VEHICLES` | `T2 > T3 > T4` | Fleet-constrained days, capacity planning |
| `MIN_COST` | `T2 > T3+T4` | Normal operations -- a vehicle is deployed iff its fixed cost is repaid |
| `MIN_DURATION` | as `MIN_COST`, time only | Driver-hour-constrained operations |
| `MAX_SERVICE` | as `MIN_COST`, orders stay **required** | Peak days, SLA protection |
| `PRIZE_COLLECTING` | `T1 > T2+T3+T4` | Capacity-scarce and marketplace models |

The two rows to read twice are the last two. An implementation that treats every
mode as strictly lexicographic passes most tests and is still wrong in both
places: `MIN_COST` collapses into `MIN_VEHICLES` because one fewer vehicle
always wins, and `PRIZE_COLLECTING` can never drop anything.

## 10. Sixteen invariants and an independent verifier

This is the part of the platform that most distinguishes it, and it comes
straight from the constitution's first principle:

> **CON-1 -- Feasibility is not negotiable; optimality is.** A plan that
> violates a hard constraint is worthless no matter how cheap it is. The system
> must never emit a plan claimed feasible without it passing an independent
> checker that does not share code with the solver.

| | Checks |
|---|---|
| `INV-1` | Every order appears exactly once across routes and unassigned |
| `INV-2` | A shipment's pickup and delivery are on the same route, pickup first |
| `INV-3` | Per step: arrival <= start of service; start + service = departure |
| `INV-4` | Arrival chains correctly through the **pinned** matrix version |
| `INV-5` | Load stays within capacity on every dimension at every step |
| `INV-6` | Route duration, distance and shift bounds hold |
| `INV-7` | The driving-hours timeline satisfies the active rule set |
| `INV-8` | Every lock is satisfied exactly |
| `INV-9` | The objective recomputed from the routes equals the reported objective |
| `INV-10` | No route carries an order whose skills, class or access its vehicle lacks |
| `INV-11` | Reloads happen only where permitted, no more often than allowed |
| `INV-12` | No depot dispatches more vehicles in a slot than it has bays |
| `INV-13` | No depot supplies more than it holds, counted **globally** |
| `INV-14` | No shipment is aboard longer than its max ride time |
| `INV-15` | Coupled routes actually meet as their synchronisation requires |
| `INV-16` | An electric vehicle never arrives past empty, and charges only at its own chargers |

**`INV-9` is called the single most valuable test in the system.** Most silent
optimisation bugs are an evaluator that disagrees with itself; recomputing the
objective from the plan catches them.

**`INV-10`--`INV-16` are numbered past the original nine deliberately.** Each was
added when a real constraint turned out to have no invariant watching it.

**Does the verifier actually catch things?** Measured rather than asserted:
`experiments/e06_mutation.py` builds a verified plan from real road distances
and seeds six defects into it. **Six of six caught, each naming the right
invariant.** The clean plan passes.

The evaluator (`vrp/evaluator.py`, 504 lines) and the verifier
(`vrp/verify/verifier.py`, 806 lines) are separate on purpose: the verifier must
not import the evaluator used inside local search, and must be written by a
different author. Discrepancies between the two are P1 defects.

## 11. The constraint catalogue

What the platform can express that the gateway cannot:

| Area | Module | Substance |
|---|---|---|
| Capacity | `model` | Multiple simultaneous dimensions; peak load, not total |
| Time windows | `model` | Multiple disjoint windows per stop; hard or soft with per-second penalties |
| Service time | `model`, `calibrate` | Fixed plus per-unit; fitted from telematics |
| Time-dependent travel | `timedependent`, `speedfit` | Piecewise-constant speed by arc class and time bucket, FIFO-preserving |
| Driving hours | `hos/` | A rules engine, not a duration cap |
| Skills and access | `model`, `diagnose` | Required skills, order-class incompatibility, site access classes, vehicle weight limits |
| Locks | `locks`, `triggers` | Operator instructions as hard constraints; infeasible lock sets returned as a **minimal conflicting set** |
| Consistency | `consistency` | Same driver, same territory, day over day -- treated as value, not concession |
| Multi-trip | `model` | Reload locations, reload counts, reload duration |
| Synchronisation | `synchronise` | Two routes meeting, with min and max gap |
| Depot inventory | `depots` | Global stock, counted across every route drawing on it |
| Electric vehicles | `electric`, `battery` | Range, charger locations, tapering charging curves |
| Prizes and priority | `pcdispatch`, `model` | Priority tiers, prizes, droppable versus required orders |

## 12. Solvers, and why there are several

| Layer | Module | Role |
|---|---|---|
| Portfolio | `portfolio` | Runs several engines, scores them on one canonical objective |
| Adapters | `solve/pyvrp_adapter.py`, `solve/ortools_adapter.py` | Mature cores preferred over bespoke ones -- constitution `CON-10` |
| Ruin and recreate | `lns` | SISR: remove related work, rebuild it better |
| Local search | `localsearch` | O(1) move evaluation, the largest determinant of local-search throughput |
| Set partitioning | `setpartition` | Collect every distinct route generated, then solve an exact cover over the pool |
| Polish | `polish` | Per-route exact passes after the metaheuristic's budget is spent |
| Decomposition | `decompose` | Partition huge instances, re-optimise sub-problems against an incumbent, repair the seams |

The architectural point in the specification is worth quoting: **the solver is
the small part.** Of the eight layers, the ones that hold durable value are the
domain model, the matrix subsystem, the evaluator, the verifier and the
calibration loop. The solver layer is explicitly "replaceable".

## 13. Dynamic operation -- the day after the plan

A plan meets reality in the first hour. This is the machinery for that:

| Module | Answers |
|---|---|
| `epochs` | Dispatch waves: what must go now, what can wait for the next wave |
| `policies` | Greedy, lazy and random baselines to measure a real policy against |
| `pcdispatch` | Each epoch as a prize-collecting VRPTW -- the prize encodes how much we want it dispatched now |
| `icd` | Sample future demand, solve each scenario, dispatch what most scenarios agree on |
| `committed` | What has already been executed and may never be replanned |
| `triggers` | Locked re-optimisation: a breakdown at 11:00 replans the affected work and nothing else |
| `stability` | Churn -- measuring it, pricing it, and deciding how much to pay for a stable plan |
| `quote` | The price of inserting or removing one order, without replanning |
| `replay` | Replays historical days epoch by epoch to evaluate a policy offline |

## 14. Learning from what actually happened

Constitution `CON-6`: trust the plan only as far as it survives contact with
reality. Plan quality is measured against executed GPS traces, not against the
plan's own estimate.

| Module | Fits |
|---|---|
| `adherence` | Telematics ingestion; where the plan and the day diverged |
| `calibrate` | Service duration as a function of archetype, quantity, vehicle, time of day |
| `speedfit` | Per-arc-class speed multipliers against the engine's free-flow assumption |
| `zones` | The zone-sequence prior learned from rounds drivers actually ran |

## 15. Operating a planner responsibly

| Module | Provides |
|---|---|
| `snapshot` | Immutable inputs, config and output; a plan replayable from its snapshot |
| `observe` | The run record: objective trajectory, incumbent timestamps, violation counts, cache hit rate, seed |
| `explain` | Per order: why this vehicle, this position, this time -- and what it would take to change it |
| `diagnose` | Pre-flight infeasibility, by an explicit diagnostic pass rather than inference |
| `rollout` | Shadow mode and canary staging |
| `anonymise` | The gate any corpus derived from real deliveries passes before leaving the boundary |
| `modelcheck` | The gate a JSON-configured delivery model passes before it ships |
| `benchmarks` | Reading public instances -- CVRPLIB, Solomon, Li & Lim |

---

# Part III -- Use cases and scenarios

## 16. Which tool for which question

| Business question | Use | Why |
|---|---|---|
| "Which warehouse should serve this customer?" | `POST /vrp/allocate` | Territories without paying for a TSP |
| "What order should this driver run today?" | `POST /vrp` or `POST /trip` | Sequencing is the gateway's strength |
| "Split 600 drops across our 4 depots and 20 vans" | `POST /vrp` | Allocation plus chunking is exactly this |
| "Route around the customer's 2--5pm window" | `vrp/` platform | The gateway has no clock |
| "This drop takes 20 minutes, that one takes 2" | `vrp/` platform | Service time is a domain-model field |
| "The van holds 800 kg and 12 pallets" | `vrp/` platform | Multi-dimensional capacity |
| "Only certified techs can do this job" | `vrp/` platform | Skills and access classes |
| "A van broke down at 11:00" | `vrp/` platform -- `committed` + `triggers` | Replan the affected work, freeze the rest |
| "How many vans do we need next quarter?" | `vrp/` platform -- `scenarios`, `fleet` | Fleet sizing over a scenario set |
| "Why was this order left unassigned?" | `vrp/` platform -- `explain`, `diagnose` | Explainability is a product requirement (`CON-5`) |
| "Is this plan, from any system, actually legal?" | `vrp/api.py` `/verify` | Deliberately public and solver-independent |
| "Is our planner getting worse?" | `vrp/` platform -- `adherence`, `rollout` | Measured against GPS, not against itself |

## 17. Five worked scenarios

### 17.1 Metropolitan parcel round -- the gateway's home ground

600 drops, four depots around the GAM, vans that hold about 40 stops a shift.
No windows; the customer gets an SMS when the driver is close.

```bash
curl -s localhost:8000/v1/vrp -H 'content-type: application/json' -d '{
  "depots": [ ...4 depots with ids... ],
  "stops":  [ ...600 stops with ids... ],
  "capacity": 40,
  "clustering_mode": "travel_time",
  "max_radius_km": 30
}'
```

You get roughly 15--16 routes labelled `D1-1`, `D1-2`, ..., each with an
optimised sequence, a geometry to draw, and its own distance and duration.
Anything outside 30 km of every depot comes back from `/vrp/allocate` as
unreachable rather than being quietly attached to a route.

**Check first, then commit.** Run `/vrp/allocate` on the same body and look at
the territories before you run the full solve. It is cheaper and it is where
data problems surface.

### 17.2 Signed documents -- where the gateway is the wrong tool

A courier hands over an envelope, waits while the customer signs, and rides on.
Ten minutes a stop, every stop. The envelope weighs 200 g and comes back, so the
satchel never changes size.

`examples/src/fleet/tw/envelope_round.py` measures this round and the split is
not close: **signing takes the overwhelming majority of the day, riding a low
single-digit percentage**, and the remainder is waiting for offices to open at
eight and reopen at one.

Everything the gateway optimises is rounding error here. The only real decision
is how many couriers to send -- which needs service time, business hours as
disjoint windows, and a capacity dimension that is not kilograms. All three are
domain-model fields; none is expressible in a `/vrp` request.

The example also shows why kilograms would not help: 200 g rounds up to 1 kg, a
fivefold overstatement, and so does every other envelope, so the dimension would
carry no information at all.

### 17.3 A vehicle breaks down at 11:00

The user story behind the dynamic layer: *re-optimise only the affected and
nearby work, while everything already executed stays executed.*

`vrp/committed.py` holds what has already happened and may never be replanned.
`vrp/triggers.py` runs the locked re-optimisation and reports the delta.
`vrp/stability.py` prices the churn -- stops moved between vehicles, ETAs that
must be re-communicated -- so that "better" does not silently mean "twelve
customers get a second phone call".

See `examples/src/fleet/dynamic/breakdown_at_eleven.py` and `churn_tradeoff.py`.

### 17.4 How many vans next quarter

`vrp/scenarios.py` takes a scenario set of historical or generated demand days
and recommends the fleet composition that minimises expected total cost --
acquisition or lease, plus routing, plus the cost of the days it cannot serve.

`vrp/fleet.py` is the related but distinct procedure for when vehicle count is
*the* objective, kept separate deliberately because minimising fleet and
minimising cost are different searches.

See `examples/src/fleet/alloc/tactical_sizing.py`, `fleet_minimisation.py` and
`fleet_mix.py`.

### 17.5 Checking someone else's plan

`/verify` is deliberately public. It lets integrators check plans produced
elsewhere, and it forces the verifier to be genuinely independent of the solver
-- a verifier that can only check its own solver's plans shares that solver's
assumptions, and those assumptions are where the bugs hide.

The parser refuses rather than helps: it will not infer a missing arrival,
coerce `"600"` to `600`, or default an absent window. Being helpful would
produce a report about a plan the integrator did not send, and it would pass --
which is worse than failing.

See `examples/src/fleet/verify/external_plan.py`.

---

# Part IV -- Reference

## 18. Runnable examples

Every example is a real client script. Run the menu with `make examples`, or one
directly with `uv run --package osrm-api-gateway-examples examples/src/<path>`.

**The gateway's `/vrp`**

| Script | Shows |
|---|---|
| `fleet/visualize_vrp.py` | A solved plan drawn on a map |
| `fleet/clustering_mode_comparison.py` | The same data through all three modes, side by side |
| `fleet/hysteresis_demo.py` | What the band actually holds |
| `fleet/stress_test_vrp.py` | Behaviour at the capacity guards |
| `clustering/run_clustering_workflow.py` | The allocate-then-route workflow |
| `clustering/simple_id_example.py` | Custom stop ids through the whole round trip |
| `benchmarking/compare_tsp.py` | Sequencing against alternatives |

**The platform: constraints**

| Script | Shows |
|---|---|
| `fleet/p0/must_work_at_v1.py` | The fourteen operations that must work at v1, each beside the naive answer it breaks |
| `fleet/rich/multi_capacity.py` | Several capacity dimensions at once |
| `fleet/rich/heterogeneous_fleet.py` | Mixed vehicles |
| `fleet/rich/skills_and_access.py` | Skills, order class, site access |
| `fleet/rich/hours_of_service.py` | Driving hours as a rules engine |
| `fleet/rich/time_dependent.py`, `planning_under_congestion.py` | Travel that depends on departure time |
| `fleet/rich/multi_trip.py`, `ride_time.py`, `synchronisation.py` | Reloading, ride-time caps, coupled routes |
| `fleet/rich/ev_recharging.py` | Range and charging curves |
| `fleet/rich/locks_and_overrides.py` | Operator intent as a hard constraint |
| `fleet/rich/prizes_and_priority.py`, `priority_sources.py` | Droppable work and why it was dropped |
| `fleet/tw/multiple_windows.py`, `sla_windows.py`, `envelope_round.py` | Windows in three flavours |

**The platform: fleet and allocation**

`fleet/alloc/` -- `territories.py`, `fleet_minimisation.py`, `fleet_mix.py`,
`tactical_sizing.py`, `depot_inventory.py`

**The platform: dynamic operation**

`fleet/dynamic/` -- `breakdown_at_eleven.py`, `committed_state.py`,
`dispatch_waves.py`, `prize_collecting_epoch.py`, `replay_policies.py`,
`sample_scenario_policy.py`, `insertion_quote.py`, `churn_tradeoff.py`,
`preemption.py`

**The platform: explanation, learning, infrastructure**

`fleet/explain/` -- `why_unassigned.py`, `preflight_diagnosis.py`
`fleet/learn/` -- `service_time_calibration.py`, `speed_calibration.py`,
`zone_sequence_prior.py`, `plan_adherence.py`, `canary_rollout.py`
`fleet/infra/` -- `run_record.py`, `plan_snapshots.py`, `degraded_matrix.py`,
`decomposition_queue.py`, `portfolio_parallelism.py`, `accelerator_profile.py`

## 19. Named problem classes

For anyone who speaks in literature terms rather than capabilities:

| Variant | Composed of | Benchmark set |
|---|---|---|
| **TSP** | One vehicle, no binding capacity or windows | Exercised through CVRP |
| **CVRP** | Capacity, fleet, depot | CVRPLIB / Uchoa |
| **VRPTW** | CVRP plus windows, service duration, shift windows | Solomon, Gehring & Homberger |
| **MDHVRPTW** | VRPTW plus per-vehicle capacity, cost and profile, multiple depots | Cordeau MDVRPTW |
| **PDPTW** | VRPTW plus shipments with precedence and same-vehicle | Li & Lim |

**MDHVRPTW is the target shape for this business** -- several depots, mixed
vehicles, customer windows. Anything treating the fleet as homogeneous or the
depot as singular is a stepping stone, not a deliverable.

**TSP is not a separate workstream.** A single uncapacitated vehicle with
unbounded windows *is* a TSP, and it is the one variant already in production:
the gateway's `/vrp` delegates sequencing to OSRM's `/trip`, which is a TSP
solver.

## 20. Glossary

| Term | Meaning here |
|---|---|
| Allocation | Assigning stops to depots. The gateway's phase 2 |
| Sequencing | Ordering one vehicle's stops. The gateway's phase 4 |
| Chunk | One vehicle's load: a contiguous slice of the sweep order |
| Anchor | The nearest depot by straight-line distance |
| Hysteresis | The margin a road-cost-better depot must beat the anchor by |
| Sweep order | Stops sorted by bearing around their depot |
| Pinned matrix | Travel data captured with a version hash, so a plan is reproducible |
| Incumbent | The best plan found so far; an anytime solver always has one |
| Churn | Stops moved between vehicles, and ETAs that must be re-communicated |
| Must-go | Work that cannot wait for the next dispatch wave |
| Epoch | One dispatch wave in a same-day operation |
| Lexicographic | Strictly ordered tiers: no amount of a lower tier buys any of a higher one |

## 21. Honest status, and the stale documents

- **The gateway's `/vrp` is in production** and does what Part I describes.
- **The platform is 84 of 86 backlog tasks done**, two blocked: `T-84` (cuOpt)
  on GPU hardware, and `T-99` (production `P1`/`P2` fixtures) on a live operation
  supplying records. Neither is blocked on effort. It is a library; nothing hosts
  it as a service, and how the Rust gateway would reach a Python solver is an
  open architectural question the specification does not answer.
- **`vrp` is now an installable distribution** — `vrp-platform` 0.3.1, built with
  hatchling. The solvers are extras, and `vrp/bench` is excluded from the wheel
  because it resolves paths against a repository root an installed copy has no
  reason to have.
- **`docs/planning/VRP_SDD_FIT_GAP.md` (2026-08-25) reports that no functional
  requirement is met.** That was true of the gateway's `/vrp` before most of
  `vrp/` existed, and it now reads as a verdict on the whole system. Treat it as
  a historical record of the gateway, not a status of the platform.
- **`SDD.md` §6 says "the VRP is capacity and geography only".** That is
  accurate about the gateway and inaccurate about the platform. Both statements
  in this document are meant to be read together.
- **Settings counts in prose disagree** (29, 35, 36). `gateway/src/config.rs` is
  the source of truth.

## 22. Where to read next

| For | Read |
|---|---|
| What a VRP is at all | Part 0 of this document |
| Request and response shapes | `docs/API_REFERENCE.md` |
| Clustering modes and hysteresis | `docs/features/clustering_modes.md` |
| The gateway's design and its limits | `docs/SDD.md` |
| The platform's specification | `docs/vrp-spec-driven-development.md` |
| Why road distance is not geometry | `docs/whitepapers/01-routing-a-delivery-day.md` |
| What the gateway costs, measured | `docs/whitepapers/02-what-the-gateway-costs.md` |
| Feasibility as a gate, and the verifier under mutation | `docs/whitepapers/03-feasibility-is-a-gate.md` |
| The scenario catalogue | `docs/TDD/vrp-catalogue-v2.1.md` |
| Deploying and operating it | `docs/golive/GO_LIVE_PLAN.md`, `docs/RUNBOOK.md` |

---

*Prepared 2026-09-12. Algorithm descriptions were read from
`gateway/src/vrp/allocate.rs`, `gateway/src/vrp/solve.rs`, `gateway/src/models.rs`
and `vrp/`; measured figures are from the whitepaper experiments, whose scripts
and JSON output are committed under `docs/whitepapers/experiments/`.*
