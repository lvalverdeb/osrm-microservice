# Delivery dataset preparation

How the Costa Rica planned-delivery dataset is built, so it can be reproduced,
resized or re-shaped without guessing at the choices behind it.

Generator: [`../examples/src/fleet/generate_delivery_dataset.py`](../examples/src/fleet/generate_delivery_dataset.py)

---

## 1. What it produces

50,000 planned deliveries across the seven provinces of Costa Rica, weighted
towards the Greater Metropolitan Area, **every one of them reachable by road**.

```json
{
  "meta":     { "count": 50000, "seed": 20260825, "gam_share_actual": 0.72, ... },
  "depots":   [ { "name": "Guadalupe (San Jose)", "latitude": 9.9472, ... } ],
  "deliveries": [ { "product_id": "CR-20260825-000001", ... } ]
}
```

`deliveries` is a flat array of same-shaped objects, so it loads directly:

```python
import json, pandas as pd
data = json.load(open("data/deliveries_cr.json"))
df = pd.DataFrame(data["deliveries"])          # (50000, 12), no nested columns
depots = pd.DataFrame(data["depots"])
```

### Record shape

| Field | Type | Notes |
|---|---|---|
| `product_id` | `str` | **The tracking key.** `CR-{seed}-{n:06d}`, unique across the file and stable for a given seed |
| `order_id` | `str` | `ORD-{n:06d}`. Separate from `product_id` so a later revision can put several products on one order without renumbering |
| `province` | `str` | One of the seven |
| `hub` | `str` | The town the delivery was sampled around |
| `gam` | `bool` | In the Greater Metropolitan Area |
| `latitude`, `longitude` | `float` | **Snapped to the road network**, 6 dp |
| `category` | `str` | Groceries, Beverages, Household, Electronics, Pharmacy, Apparel, Hardware |
| `weight_kg` | `float` | Category-dependent range; the natural capacity dimension |
| `units` | `int` | 1–6 |
| `priority` | `str` | `standard` (70%), `express` (20%), `scheduled` (10%) |
| `service_minutes` | `int` | Dwell time at the stop |

`product_id` is deliberately independent of position in the file. Sorting,
filtering or splitting the frame never invalidates it, which is what makes it
usable as the join key across allocation, sequencing and execution.

---

## 2. Reproducing it

The generator needs a running OSRM instance with Costa Rica data. Building that
is the slow part and only needs doing once.

```sh
# 1. Map data (~38 MB download, a few minutes to process)
make download-data

cd data
osrm-extract -p /opt/homebrew/share/osrm/profiles/car.lua costa-rica-latest.osm.pbf
osrm-partition costa-rica-latest.osrm
osrm-customize costa-rica-latest.osrm

# 2. Engine
osrm-routed --algorithm mld costa-rica-latest.osrm --port 5000

# 3. Dataset
uv run --package osrm-api-gateway-examples \
    examples/src/fleet/generate_delivery_dataset.py \
    --engine http://127.0.0.1:5000
```

The profile path is Homebrew's. On another platform, point `-p` at whatever
`car.lua` your OSRM install ships.

### Options

| Flag | Default | Effect |
|---|---|---|
| `--count` | `50000` | Records to produce |
| `--seed` | `20260825` | Same seed, same dataset, byte for byte |
| `--gam-share` | `0.72` | Share of deliveries inside the GAM |
| `--engine` | *(none)* | OSRM base URL. **Without it, points are not snapped and some will not be reachable by road** |
| `--max-snap-metres` | `250` | A candidate whose nearest road is further than this is discarded |
| `--out` | `data/deliveries_cr.json` | Output path |

---

## 3. The choices, and why

### Depots and hubs are borrowed, not invented

Both tables are taken verbatim from
`examples/src/clustering/run_clustering_workflow.py`: six warehouses and 31
destination hubs across the seven provinces, each hub already carrying a
`valle_central` flag.

Reusing them is the point. A plan built from this dataset can be compared
directly against that example's output, and there is one place to change a
depot rather than two that drift apart.

### Road-reachability is verified, not assumed

Sampling points in a bounding box around Costa Rica puts deliveries in the
Pacific, in Braulio Carrillo, and on the slopes of Irazú. Filtering by a
country polygon removes the ocean and nothing else.

So every candidate is snapped through OSRM `/nearest`, and:

- a point whose nearest road is more than `--max-snap-metres` away is
  **discarded**, not corrected — it stood somewhere no vehicle goes;
- the **snapped** coordinate is written, not the original, so a consumer
  routing to it does not pay the snap again and gets the same road segment the
  generator validated.

Roughly a third of rural candidates are discarded this way, which is the filter
working rather than a defect. Verified on the generated file: **120 of 120 randomly chosen deliveries were
routable by road from their nearest depot**, and 24,408 candidates were
discarded along the way.

Snapping to a road is necessary but not sufficient — a point can snap to an
isolated segment. The routability check above is what closes that gap, and is
worth re-running after any change to the hub table.

### The GAM share is measured on output, not candidates

The first implementation weighted the *draw* 72/28 and produced **85.4%** GAM in
the output. Rural candidates are far likelier to be discarded for having no road
near them, so the survivors skewed metropolitan by thirteen points.

Quotas are therefore filled against accepted records: draw from whichever class
still owes records, and drop a surplus if a quota fills mid-batch. `--gam-share`
now means what it says — measured 72.0% against 72% requested.

This is the kind of error that would not have shown up in a spot check of the
output. It is worth re-checking `meta.gam_share_actual` after any change to the
spread constants or the snap threshold.

### Spread around a hub

Gaussian, not uniform: density falls off from the town rather than filling a
square evenly. Longitude is divided by `cos(latitude)` so the spread is circular
on the ground rather than stretched by the projection.

| Class | σ (degrees) | Roughly |
|---|---|---|
| GAM | 0.045 | 5 km — dense, built up, a hub is a suburb |
| Rural | 0.13 | 14 km — a hub stands in for a whole canton |

### Product mix

Category weights and their weight ranges are plausible retail proportions, not
measured ones. They exist so a consumer has a capacity dimension that varies
sensibly and something to group by. Change them freely; nothing depends on the
specific values.

---

## 4. Distribution produced

At the committed defaults (50,000 records, seed 20260825, 72% GAM):

| Province | Share |
|---|---|
| San Jose | 27.7% |
| Alajuela | 20.0% |
| Heredia | 19.0% |
| Cartago | 17.3% |
| Guanacaste | 6.0% |
| Limon | 5.6% |
| Puntarenas | 4.3% |

Measured on the committed run, not estimated.

The four Valle Central provinces carry the bulk, which is what "heavier around
the METRO area" means in practice — the GAM spans San José, Heredia, Cartago and
part of Alajuela, so weighting the metro area necessarily weights those four.

Guanacaste, Limón and Puntarenas are represented but thin. If a use case needs
more coverage outside the valley, lower `--gam-share`; the province mix follows.

---

## 5. Notes for consumers

- **The output is not committed.** It is ~12 MB of generated data, fully
  reproducible from the generator and a seed, so committing it would add churn
  to every diff for no information. `data/` is where it lands.
- **`meta` travels with the data.** Seed, requested and actual GAM share,
  whether snapping ran, and how many candidates were discarded — enough to tell
  whether a given file was built the way you think it was.
- **Splitting for a VRP request**: the gateway caps a solve at `VRP_MAX_STOPS`
  (2,000 by default), so 50,000 deliveries is a planning corpus rather than one
  request. Filter by province or hub to get a solvable slice.
- **Weight is the obvious capacity dimension**, but the gateway's `/vrp` takes
  a single scalar `capacity` counting stops, not kilograms. Multi-dimensional
  capacity is [a gap](planning/VRP_SDD_FIT_GAP.md), not a feature — the field is
  here for when it is not.
