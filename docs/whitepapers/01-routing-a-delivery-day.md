# Road Distance Is Not Geometry

**A measured introduction for newcomers.** No prior knowledge of routing or OSRM
is assumed.

Every figure below was produced by a script in
[`experiments/`](experiments) against the live gateway at `10.211.55.33:8000`
and the 50,000-delivery Costa Rica corpus in `data/deliveries_cr.json`. Nothing
is transcribed by hand — each table names the script that made it, and re-running
that script reproduces it.

Companions: [02 — What the Gateway Costs](02-what-the-gateway-costs.md),
[03 — Feasibility Is a Gate](03-feasibility-is-a-gate.md).

---

## 1. The claim this system is built on

You have a warehouse, some vans, and two hundred addresses. Two questions must
be answered before anyone drives:

1. Which van takes which addresses?
2. In what order does each van visit them?

Both look like geometry. The obvious approach — nearest warehouse, then
nearest-first — treats the map as a plane and distance as a straight line. This
system does not, and the reason is not aesthetic. It is that straight lines are
wrong by a margin large enough to change every decision built on them.

That is a measurable claim, so we measured it.

---

## 2. Experiment: how wrong is a straight line?

`experiments/e01_geometry.py` draws 70 delivery addresses from the corpus,
requests the full road-distance matrix from the gateway, and compares each of
the ~4,800 ordered pairs against its great-circle distance. Pairs closer than
100 m are excluded, because below that the ratio is dominated by road-snapping
rather than by geography.

**Detour ratio — road distance ÷ straight-line distance**
(`results/e01_detour.json`)

| Sample | median | mean | p90 | p99 | max |
|---|---|---|---|---|---|
| Greater Metropolitan Area | **1.41** | 1.54 | 1.90 | 3.41 | **17.63** |
| Nationwide | **1.47** | 1.54 | 1.89 | 2.70 | 6.50 |

Read the median first: **a straight line understates a real drive by about 40%,
typically.** That alone invalidates capacity planning done on a map.

Then read the tail, which is where plans actually fail. One GAM pair in a
hundred is more than 3.4× its straight-line distance. And the worst pair found:

```
ORD-046107   9.960301, -84.157263
ORD-016361   9.967983, -84.160242
             914 m apart in a straight line
          16,117 m apart by road          — a ratio of 17.6
```

Two addresses you could see from each other, 16 km of driving between them. A
planner working in straight lines puts those on the same van, adjacent in the
sequence, and is confidently wrong twice: wrong about which van, wrong about
the order. Nothing in the plan looks unusual. The driver finds out.

---

## 3. Experiment: the road is not symmetric either

The same matrices answer a second question. For each unordered pair, how much
does the drive there differ from the drive back?

**Asymmetry — |there − back| ÷ their mean** (`results/e01_asymmetry.json`)

| Sample | median | p90 | max | pairs differing >1% | >10% |
|---|---|---|---|---|---|
| Greater Metropolitan Area | 2.2% | 9.1% | **44.7%** | **73%** | 8.8% |
| Nationwide | 1.7% | 8.5% | **52.7%** | 62% | 7.5% |

The headline is the "differing >1%" column: **for roughly two-thirds of pairs,
the drive out and the drive back are not the same length.** The extreme case
nationwide is two addresses 192 m apart where one direction is 526 m and the
other is 903 m — a one-way system, doing exactly what one-way systems do.

Formally, `d(i,j) ≠ d(j,i)`. The practical consequence is that a system storing
one number per pair of addresses has already discarded information it needs, and
no amount of later cleverness recovers it. This is why the matrix in this system
is a full square rather than a triangle, and why `TravelMatrix` is indexed by
ordered pair throughout.

---

## 4. What follows from those two tables

Everything structural in this repository is downstream of them.

- **There must be a real road engine.** You cannot compute the 17.6× pair from
  coordinates. Something has to have read the map.
- **Distances must be fetched, not derived**, which makes the *matrix* — one
  fetch of every pair — the central data structure, and its size the central
  constraint.
- **Both directions must be kept.**
- **Sequencing cannot be greedy on straight lines.** Nearest-first on a plane
  chooses the 914 m neighbour and drives 16 km.

---

## 5. The four jobs

Every road-routing system does these. Knowing their names makes the codebase
legible.

| Job | What it means | Endpoint |
|---|---|---|
| **Snapping** | Addresses sit in buildings and fields; vehicles drive on roads. Snapping moves each point to the nearest drivable segment and records how far it moved. | `/nearest` |
| **Measuring** | Duration and distance for every ordered pair. For *n* points, *n²* answers. | `/matrix` |
| **Allocating** | Which depot is responsible for each stop. | `/vrp/allocate` |
| **Sequencing** | The order one vehicle visits its stops — the travelling-salesperson problem. | `/trip` |

Snapping deserves more attention than it gets. Asking the gateway for the
nearest road to a point in central San José:

```
POST /nearest   {"coordinate": {"longitude": -84.0907, "latitude": 9.9281}}
→ location [-84.090271, 9.928567]   distance 69.87
```

The point moved **69.9 m**. That happens whether or not anybody looks; the only
question is whether the distance is recorded. A stop 2 km from the nearest road
still produces a perfectly plausible matrix — every number in it is a real drive
between real road points — but not a plan that serves the address anyone meant.
The corpus was built with this in mind: its metadata records
`max_snap_metres: 250`, and that reaching 50,000 usable addresses meant
**rejecting 24,408 as unreachable** (plus 3,100 and 21,308 on
catchment rules). Of 98,816 candidates, **49% were discarded before a
single plan was made**, and half of those discards were addresses the
road network could not reach at all. Those rejections are the
data-quality step, done once, visibly.

---

## 6. The pieces

```
  your app                    gateway                       engine
┌──────────┐   JSON     ┌──────────────────┐   HTTP    ┌──────────────┐
│  client  │ ─────────► │ validate → limit │ ────────► │ osrm-routed  │
│          │ ◄───────── │ → cache → relay  │ ◄──────── │    (C++)     │
└──────────┘            └────────┬─────────┘           └──────────────┘
                                 │                            │
                          ┌──────▼──────┐              ┌──────▼──────┐
                          │    Redis    │              │  Costa Rica │
                          │ cache +     │              │  map data   │
                          │ rate limits │              └─────────────┘
                          └─────────────┘
```

**The engine** (`osrm-routed`) has read the map and precomputed a great deal
about it. It is fast and has no opinions about who may ask it for a
5,000×5,000 matrix.

**The gateway** ([`gateway/`](../../gateway), Rust) is what clients talk to. It
validates, rate-limits, caches, translates JSON into the engine's URL form, and
relays the engine's bytes back **without decoding them** — so the numbers you
receive are the numbers the engine produced. It also computes two things the
engine cannot: a graph from a matrix, and a vehicle-routing plan.

**Redis** is a cache and a shared rate-limit counter, never a database. Losing
it costs hit rate and nothing else; the FreeBSD deployment ran for weeks with
Redis unreachable and served traffic throughout.

There is a fourth piece, [`vrp/`](../../vrp) — a Python planning platform with
time windows, driver hours, and an independent verifier. It is a library, not a
running service. [Paper 03](03-feasibility-is-a-gate.md) is about it, and
measures what it is worth.

---

## 7. Your first hour

```sh
make download-data              # ~180 MB Costa Rica extract
make process-osrm PROFILE=car   # compile it for one profile — the slow step
make compose-up                 # build and start all three services
make compose-health
make examples                   # interactive menu; several draw HTML maps
```

`make process-osrm` applies a **profile**, the rules for what counts as
drivable. The `car` profile obeys one-way signs and refuses footpaths. The
choice is made here, at compile time, not per request — which matters in the
next section.

Two traps: the compose file is not at the repository root, so a bare
`docker compose up` will not find it; and if `DOCKER_HOST` points at a remote
daemon, published ports are on *that* host, not on localhost.

---

## 8. Your first request

```python
import requests

response = requests.post("http://localhost:8000/route", json={
    "origin":      {"longitude": -84.0907, "latitude": 9.9281},
    "destination": {"longitude": -84.0833, "latitude": 9.9333},
    "profile": "driving",
    "steps": True,
})
```

Three things worth knowing.

**Longitude first.** Latitude is north–south, longitude east–west. The JSON
names both so you cannot get it wrong, but raw OSRM URLs use `lon,lat` — the
reverse of the `lat,lon` convention on mapping websites. This is the most common
beginner error in the domain.

**`steps: True` asks for turn-by-turn instructions.** Without it you get
geometry and totals, which is smaller and usually what a planner wants.

**`profile` is accepted and ignored by the engine.** Each `osrm-routed` serves
exactly one profile, fixed by `make process-osrm`. Asking for `walking` against
a `car` engine returns a car route. A heterogeneous fleet needs several engines.
This is documented rather than hidden, which is the standard to expect from the
rest of the docs.

---

## 9. The endpoints

Eleven functional endpoints, plus four serving the schema and its docs UI.

**Relayed from the engine:** `/route` (drive from A to B), `/matrix` (all pairs),
`/nearest` (which road is this on), `/match` (snap a noisy GPS trace to the
roads it was really on), `/trip` (order these stops),
`/tile/{profile}/{z}/{x}/{y}` (the routing graph as a protobuf map tile).

**Computed by the gateway:** `/matrix-graph` (a matrix as a node-link graph),
`/vrp/allocate` (which depot serves each stop), `/vrp` (allocate and sequence —
a full plan).

**Operational:** `/health` always 200 with detail; `/ready` returns 503 when the
engine is unreachable, so a balancer drains the node instead of feeding it
requests it cannot serve.

---

## 10. What `/vrp` does, and what it does not

`/vrp` answers both questions from §1.

**Allocate.** Build a depot-to-stop matrix. Assign each stop to a depot by road
cost — with a straight-line anchor, a stability band, and a sanity override that
refuses an implausible matrix answer. [Paper 02 §5](02-what-the-gateway-costs.md)
measures exactly what that band does, and the answer is surprising.

**Sequence.** Sort each depot's stops by compass angle about the depot, cut them
into vehicle-sized chunks, and send each chunk to the engine's `/trip`. Chunks
run concurrently; the first failure cancels its siblings.

It is fast. Measured end to end (`experiments/e04_scaling.py`), **2,000 stops
across 58 vehicles takes 1.18 s**, and cost per stop *falls* with size, from
1.7 ms/stop at 50 stops to 0.59 ms/stop at 2,000.

It is also not an optimiser. It has no objective function and cannot compare two
plans. [Paper 03 §3](03-feasibility-is-a-gate.md) measures the gap between this
and a real solver on identical instances: **8% worse on pure sequencing, rising
to 15% with six vehicles**, because sweep-and-cut fixes each vehicle's load
before anything is optimised. Whether that matters depends on your margins; the
point is that the number now exists.

---

## 11. Vocabulary

| Term | Meaning |
|---|---|
| Engine | `osrm-routed`, the C++ router |
| Profile | Rules for what is drivable — `car`, `bicycle`, `foot`. Fixed at compile time |
| Snapping | Moving a coordinate to the nearest road, and recording how far |
| Matrix | Durations and distances between every ordered pair |
| Depot | Where vehicles start and load |
| Stop | A place that must be visited |
| Allocation | Assigning stops to depots |
| Sequencing | Ordering one vehicle's stops |
| Chunk | One vehicle's load — a contiguous slice of a depot's stops |
| Upstream | A gateway → engine request |
| L1 / L2 | In-process cache tier / shared Redis tier |
| VRP / TSP | Vehicle Routing Problem / Travelling Salesperson Problem |

---

## 12. Honest limits

- **One engine, one profile.** Mixed fleets need several engines.
- **No time dimension.** OSRM has no departure-time parameter, so every plan
  assumes free-flow speed — no rush hour. This bounds what any plan built on it
  can claim.
- **Coordinates travel in the URL**, OSRM's contract, capping `/match` at
  roughly 720 points. Over-long requests get a 422 naming the limit rather than
  a dropped connection.
- **`/vrp` is capacity and geography only** — no time windows, service times,
  skills or shifts. Those are in `vrp/`, which is not yet reachable over HTTP.
- **`process_*` metrics are Linux-only**, read from `/proc`; the FreeBSD jail
  reports none.

---

## 13. Reproducing this paper

```sh
export WHITEPAPER_GATEWAY=http://10.211.55.33:8000
cd docs/whitepapers/experiments
PYTHONPATH=../../.. uv run python e01_geometry.py   # §2 and §3
PYTHONPATH=../../.. uv run python e04_scaling.py    # §10
```

Results land in `experiments/results/` as JSON. Sampling is seeded
(`seed = 20260902`), so a re-run on the same corpus and the same map extract
reproduces the tables. A different extract will move them, which is the point of
recording the gateway and corpus alongside every figure.

Next: [02 — What the Gateway Costs](02-what-the-gateway-costs.md).
