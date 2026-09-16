# 42 — Endpoint guide, with real requests and responses

*Tier 2: every example executed against a live gateway on a Costa Rica graph,
using coordinates from `data/deliveries_cr.json`. The generated contract table is
[tier 1 doc 01](../tier1/01-api-reference.md); this is the worked companion.*

Every endpoint the gateway serves: what it is for, what it takes, and a real
request with the real response it returned.

> **Using the Python routing library instead of HTTP?** That is a separate
> surface: [VRP_PUBLIC_API.md](41-vrp-library-api.md).

**Nothing here is illustrative.** Every example below was executed against a
gateway running on a Costa Rica OSRM graph built from the Geofabrik extract in
`data/`, using real coordinates from the 50,000-delivery corpus in
`data/deliveries_cr.json` -- six real depots, real customer drops, real road
distances. Timings are from that run and are indicative of a warm local engine,
not of your production hardware. Long geometries are elided with the true count
of what was removed; nothing else is edited.

| | |
|---|---|
| Gateway | `gateway/` debug build |
| Engine | `osrm-routed` v26.8.0, MLD, `--max-table-size 100` |
| Graph | `costa-rica-latest.osm.pbf`, car profile |
| Corpus | `data/deliveries_cr.json` -- 6 depots, 50,000 deliveries |
| Captured | 2026-09-12 |

## Contents

| Endpoint | Answers |
|---|---|
| [`POST /v1/route`](#post-v1route) | How do I drive from A to B? |
| [`POST /v1/matrix`](#post-v1matrix) | How far is everything from everything? |
| [`POST /v1/matrix-graph`](#post-v1matrix-graph) | The same, shaped for a graph library |
| [`POST /v1/trip`](#post-v1trip) | What order should one vehicle visit these? |
| [`POST /v1/match`](#post-v1match) | Which roads did this GPS trace actually use? |
| [`POST /v1/nearest`](#post-v1nearest) | What road is this coordinate on? |
| [`POST /v1/nearest/batch`](#post-v1nearestbatch) | The same, for many coordinates |
| [`POST /v1/vrp`](#post-v1vrp) | Who takes which drops, in what order? |
| [`POST /v1/vrp/allocate`](#post-v1vrpallocate) | Which depot serves which customer? |
| [`GET /v1/tile/…`](#get-v1tileprofilezxymvt) | Draw the routing graph on a map |
| [`GET /health`, `/ready`](#get-health-and-get-ready) | Is it alive? Is it usable? |
| [`GET /metrics`](#get-metrics) | What has it been doing? |
| [`GET /docs`, `/redoc`, `/openapi.json`](#get-docs-redoc-and-openapijson) | The contract, browsable |

## Two things that apply everywhere

**Versioned and unversioned paths.** Every endpoint is served at `/v1/...` and,
during the deprecation window, at the root as well. Unversioned responses carry
`Deprecation: true` and a `Link` to their `/v1` successor. **Build against
`/v1`.**

**Shared routing options.** `route`, `matrix`, `match`, `trip`, `nearest` and
`nearest/batch` all accept these in addition to their own fields:

| Option | Type | Effect |
|---|---|---|
| `profile` | `driving` \| `cycling` \| `walking` | Which routing graph answers. A profile with no engine configured is refused by name |
| `radiuses` | array of numbers | How far each coordinate may snap |
| `bearings` | array | Restrict snapping to a heading, for known vehicle direction |
| `approaches` | array | `curb` or `unrestricted` per coordinate |
| `exclude` | array | Exclude road classes, e.g. toll |
| `snapping` | string | `default` or `any`, whether to snap to otherwise-excluded edges |
| `hints`, `generate_hints` | array / bool | Reuse snap results across calls |
| `skip_waypoints` | bool | Omit the waypoint echo to shrink the response |

---

## `POST /v1/route`

**Use case.** A driver needs to get from one place to another, and you want the
distance, the time, the line to draw on a map, and optionally the turn-by-turn
instructions. This is the endpoint behind "navigate to the next stop".

### Parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `origin` | Coordinate | **yes** | `{longitude, latitude}` |
| `destination` | Coordinate | **yes** | |
| `waypoints` | array of Coordinate | no | Intermediate stops, visited **in the order given** -- this does not reorder them; that is `/trip` |
| `steps` | bool | no | Turn-by-turn instructions |
| `alternatives` | bool or int | no | Ask for other routes; the engine may return fewer |
| `geometries` | `polyline` \| `polyline6` \| `geojson` | no | Shape encoding |
| `overview` | `simplified` \| `full` \| `false` | no | Geometry detail |
| `continue_straight` | bool | no | Forbid U-turns at waypoints |
| `annotations` | string | no | Per-segment detail |

### Real example

Guadalupe depot to customer `ORD-000008`, with turn-by-turn:

```bash
curl -s http://localhost:8000/v1/route \
  -H 'content-type: application/json' \
  -d '{"origin": {"longitude": -84.0531, "latitude": 9.9472}, "destination": {"longitude": -84.088218, "latitude": 9.909005}, "steps": true, "overview": "simplified", "geometries": "geojson"}'
```

### Real result

`200` · **17.7 ms** · 20,660 bytes

```json
{
  "code": "Ok",
  "routes": [
    {
      "distance": 7454.6,
      "duration": 631.8,
      "weight": 631.8,
      "weight_name": "routability",
      "geometry": {
        "type": "LineString",
        "coordinates": [
          [
            -84.05293,
            9.94729
          ],
          [
            -84.053238,
            9.947854
          ],
          "... 19 more coordinate pairs elided ..."
        ]
      },
      "legs": [
        {
          "distance": 7454.6,
          "duration": 631.8,
          "summary": "Calle 49, Paseo de la Segunda República",
          "steps": [
            {
              "maneuver": {
                "location": [
                  -84.05293,
                  9.94729
                ],
                "bearing_after": 332,
                "bearing_before": 0,
                "modifier": "left",
                "type": "depart"
              },
              "name": "Calle 55",
              "mode": "driving",
              "duration": 13.3,
              "distance": 70.9
            }
          ]
        }
      ]
    }
  ],
  "waypoints": [
    {
      "hint": "<opaque snap hint, elided>",
      "name": "Calle 55",
      "distance": 21.13420758,
      "location": [
        -84.05293,
        9.94729
      ]
    },
    {
      "hint": "<opaque snap hint, elided>",
      "name": "Avenida 56",
      "distance": 0,
      "location": [
        -84.088218,
        9.909005
      ]
    }
  ]
}
```

Trimmed above for readability. What actually came back:

| | |
|---|---|
| Distance | **7,454.6 m** |
| Duration | **631.8 s** (10.5 min) |
| Legs | 1 |
| Turn-by-turn steps | 16 |
| Geometry points | 21 |

The first four manoeuvres, verbatim:

```
  depart         Calle 55                  71 m
  turn           Avenida 33               299 m
  turn           Calle 49                 829 m
  rotary         Vía 202                   16 m
```

### Worth knowing

**`alternatives` sorts by time, not distance.** Asking for alternatives on a
nearby pair returned two routes: the primary at **8,118 m /
693.7 s**, and the alternative at
**7,870 m / 730.5 s**. The primary is the
*longer* of the two in metres and still the right answer, because it is faster.
Sort on the field you actually care about.

**`waypoints` keeps your order.** If you want the cheapest order, that is
`/trip`. Sending stops to `/route` in the order they arrived is the single most
common way to produce a plan that looks optimised and is not.

---

## `POST /v1/matrix`

**Use case.** Everything that plans a day needs this first: the cost of going
from each place to each other place. Territory assignment, sequencing, fleet
sizing and any external solver all start from a matrix.

### Parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `coordinates` | array of Coordinate | **yes** | 2 to 5,000 by schema; the **engine** caps the product -- see below |
| `annotations` | `duration` \| `distance` \| `duration,distance` | no | A **string**, not a list. Asking for both costs nothing extra |
| `sources` | array of int | no | Indices used as rows; omit for all |
| `destinations` | array of int | no | Indices used as columns; omit for all |
| `fallback_speed` | number | no | Fill unreachable pairs at this speed instead of `null` |
| `fallback_coordinate` | string | no | `input` or `snapped`, which point the fallback measures from |
| `scale_factor` | number | no | Multiply durations |

### Real example

The Guadalupe depot plus five real customer drops:

```bash
curl -s http://localhost:8000/v1/matrix \
  -H 'content-type: application/json' \
  -d '{"coordinates": [{"longitude": -84.0531, "latitude": 9.9472}, {"longitude": -84.088218, "latitude": 9.909005}, {"longitude": -84.07515, "latitude": 9.939902}, {"longitude": -84.055146, "latitude": 9.935129}, {"longitude": -84.055441, "latitude": 9.912299}, {"longitude": -84.059088, "latitude": 9.932245}], "annotations": "duration,distance"}'
```

### Real result

`200` · **5.9 ms** · 2,804 bytes · 6x6

```json
{
  "distances": [
    [0, 7454.6, 2847.8, 2644.2, 4918.8, 2577.8],
    [7523.9, 0, 4439.6, 5327.9, 4116.5, 5476.7],
    [2736.4, 4500.2, 0, 3666.7, 5941.3, 2948.4],
    [1623.9, 6635.4, 3102.1, 0, 4099.6, 1758.6],
    [5618.5, 4322.6, 5566.5, 3422.5, 0, 3571.2],
    [2869.9, 5503.3, 2995.7, 673.9, 2967.5, 0]
  ],
  "code": "Ok",
  "destinations": [
    {
      "hint": "<opaque snap hint, elided>",
      "name": "Calle 55",
      "distance": 21.13420758,
      "location": [-84.05293, 9.94729]
    },
    "... 5 more entries elided ..."
  ],
  "durations": [
    [0, 631.8, 362.1, 321.8, 476.1, 316.1],
    [557.2, 0, 505.4, 374, 406.3, 411.3],
    [283.2, 565.6, 0, 406, 560.3, 364.3],
    [186.9, 536, 380.1, 0, 380.3, 220.3],
    [457.6, 451.3, 564.1, 274.4, 0, 311.7],
    [259.5, 398.2, 339, 76.3, 242.5, 0]
  ],
  "sources": [
    {
      "hint": "<opaque snap hint, elided>",
      "name": "Calle 55",
      "distance": 21.13420758,
      "location": [-84.05293, 9.94729]
    },
    "... 5 more entries elided ..."
  ]
}
```

Row 0 is the depot to each of the five drops: **632 s, 362 s, 322 s, 476 s, 316 s** and
**7.5 km, 2.8 km, 2.6 km, 4.9 km, 2.6 km**.

### Worth knowing

**The cell cap is real and it is the product, not the count.**
`MATRIX_MAX_CELLS` defaults to 10,000, which is 100 coordinates square. 120
coordinates is 14,400 cells and is refused:

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": [
        "body"
      ],
      "msg": "Value error, Matrix of 14400 cells (sources x destinations) ... (150 chars)",
      "input": null
    }
  ]
}
```

Raising it means passing `--max-table-size` to `osrm-routed` as the square root
of the new value, in **both** deployment definitions. Narrowing with `sources`
and `destinations` is usually the better answer: a 1x500 depot-to-customers row
is 500 cells, not 250,000.

---

## `POST /v1/matrix-graph`

**Use case.** The same numbers as `/matrix`, already shaped as nodes and edges
so they load straight into NetworkX, a graph database, or anything else that
speaks node-link JSON. Use it when the consumer is a graph algorithm rather than
a table.

### Parameters

Identical to `/v1/matrix`.

### Real example

```bash
curl -s http://localhost:8000/v1/matrix-graph \
  -H 'content-type: application/json' \
  -d '{"coordinates": [{"longitude": -84.0531, "latitude": 9.9472}, {"longitude": -84.088218, "latitude": 9.909005}, {"longitude": -84.07515, "latitude": 9.939902}, {"longitude": -84.055146, "latitude": 9.935129}], "annotations": "duration,distance"}'
```

### Real result

`200` · **3.8 ms** · 928 bytes · 4 nodes, 12 edges

```json
{
  "directed": true,
  "multigraph": false,
  "graph": {},
  "nodes": [
    {
      "lon": -84.0531,
      "lat": 9.9472,
      "id": 0
    },
    "... 3 more entries elided ..."
  ],
  "edges": [
    {
      "duration": 631.8,
      "distance": 7454.6,
      "source": 0,
      "target": 1
    },
    "... 11 more entries elided ..."
  ]
}
```

### Worth knowing

**It is a complete directed graph**: 4 nodes produce
12 edges, because n x (n-1) ordered pairs each get their own
edge. Both directions are present and they differ -- which is the point, since
two-thirds of metropolitan address pairs in this corpus are asymmetric.

Self-pairs are omitted. An unreachable pair carries no edge at all rather than
an edge with a null weight, so a consumer can test reachability with an edge
lookup.

---

## `POST /v1/trip`

**Use case.** One vehicle, a set of stops, and the question "what order?" This
is the travelling-salesman solver. It is what `/vrp` calls once per vehicle.

### Parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `coordinates` | array of Coordinate | **yes** | **2 to 200.** This is the hard engine limit |
| `roundtrip` | bool | no | Return to the start |
| `source` | `first` \| `any` | no | `first` pins the start -- use it when coordinate 0 is your depot |
| `destination` | `last` \| `any` | no | Pin the end |
| `steps`, `geometries`, `overview`, `annotations` | | no | As `/route` |

### Real example

The depot plus six real drops, closed tour, depot pinned as the start:

```bash
curl -s http://localhost:8000/v1/trip \
  -H 'content-type: application/json' \
  -d '{"coordinates": [{"longitude": -84.0531, "latitude": 9.9472}, {"longitude": -84.088218, "latitude": 9.909005}, {"longitude": -84.07515, "latitude": 9.939902}, {"longitude": -84.055146, "latitude": 9.935129}, {"longitude": -84.055441, "latitude": 9.912299}, {"longitude": -84.059088, "latitude": 9.932245}, {"longitude": -84.089842, "latitude": 9.978181}], "roundtrip": true, "source": "first", "overview": "false"}'
```

### Real result

`200` · **11.1 ms** · 108,210 bytes

```json
{
  "code": "Ok",
  "trips": [{ "distance": 29429.6, "duration": 2854.9,
              "geometry": "… elided …", "legs": [ … 7 legs … ] }],
  "waypoints": [ … 7 entries, shown below … ]
}
```

| Total distance | Total duration |
|---|---|
| **29,429.6 m** (29.4 km) | **2,854.9 s** (48 min) |

**The answer is in `waypoints`, not in the order you sent.** Each waypoint
carries a `waypoint_index` giving its position in the optimised tour. For this
request the input order mapped to tour positions `[0, 4, 5, 2, 3, 1, 6]` -- so the vehicle
does not visit your stops 1,2,3,4,5,6 but in the order those indices describe.

Reading the tour back out means sorting the waypoints by `waypoint_index` and
dropping index 0, which is the depot. That remapping is exactly what `/vrp` does
for you.

---

## `POST /v1/match`

**Use case.** A vehicle drove somewhere and the tracker recorded noisy points.
This snaps that trace onto the road network it actually used, so you can compare
planned against executed, reconstruct a route from telematics, or clean GPS
before storing it.

### Parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `breadcrumbs` | array of GpsBreadcrumb | **yes** | 2 to 5,000 by schema; the **URL length** binds first -- see below |
| `tidy` | bool | no | Drop redundant points before matching |
| `gaps` | `split` \| `ignore` | no | Whether a time gap splits the trace into separate matchings |
| `steps`, `geometries`, `overview`, `annotations` | | no | As `/route` |

A `GpsBreadcrumb` is `{longitude, latitude, timestamp, accuracy_meters?}` --
`timestamp` is a required non-negative integer (Unix seconds).

### Real example

26 breadcrumbs 20 seconds apart, sampled along a real
route geometry and jittered off the road by up to ~4 m to imitate GPS error:

```json
{
  "breadcrumbs": [
    {
      "longitude": -84.052944,
      "latitude": 9.947262,
      "timestamp": 1757700000,
      "accuracy_meters": 8.0
    },
    {
      "longitude": -84.055182,
      "latitude": 9.945762,
      "timestamp": 1757700020,
      "accuracy_meters": 8.0
    },
    {
      "longitude": -84.054016,
      "latitude": 9.943031,
      "timestamp": 1757700040,
      "accuracy_meters": 8.0
    },
    "... 23 more breadcrumbs elided ..."
  ],
  "overview": "simplified",
  "geometries": "geojson",
  "tidy": true
}
```

### Real result

`200` · **4.1 ms** · 9,747 bytes

```json
{
  "code": "Ok",
  "matchings": [{ "confidence": 0.9312679624, "distance": 7238.4, "duration": 607.3,
                  "geometry": "… elided …", "legs": [ … 25 legs … ] }],
  "tracepoints": [ … 26 entries, one per input breadcrumb … ]
}
```

| Confidence | Matched distance | Matchings returned |
|---|---|---|
| **0.9312679624** | **7,238 m** | 1 |

### Worth knowing

**`confidence` is the field to alert on.** 0.9312679624 on a clean trace is
healthy. A low value means the matcher was guessing -- sparse points, a tunnel,
or a road the graph does not have.

**A `null` tracepoint is a dropped point**, not an error. With `tidy: true` the
matcher discards breadcrumbs it considers redundant, and the array keeps its
positional alignment with your input by nulling them.

**The real ceiling is URL length, not the 5,000-point schema.** Coordinates
travel in the path to the engine, so roughly 720 breadcrumbs build a request
line near the 24,000-byte `OSRM_MAX_URL_BYTES` limit. Past it you get a 422
naming the limit instead of a 500. Split long traces on a time gap.

---

## `POST /v1/nearest`

**Use case.** Data quality. A geocoder gave you a coordinate; this tells you
what road the router will actually use for it and how far it had to move to get
there. Run it over your customer master before you trust any plan built on it.

### Parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `coordinate` | Coordinate | **yes** | **Singular.** One point per call |
| `number` | integer >= 1 | no | How many candidate segments to return |
| `profile` | Profile | no | Snapping differs by profile -- a footpath is not a road |

### Real example

```bash
curl -s http://localhost:8000/v1/nearest \
  -H 'content-type: application/json' \
  -d '{"coordinate": {"longitude": -84.07515, "latitude": 9.939902}, "number": 3}'
```

### Real result

`200` · **1.1 ms** · 687 bytes

```json
{
  "code": "Ok",
  "waypoints": [
    {
      "nodes": [
        11125571229,
        11125571230
      ],
      "hint": "<opaque snap hint, elided>",
      "name": "",
      "distance": 0,
      "location": [
        -84.07515,
        9.939902
      ]
    },
    "... 2 more entries elided ..."
  ]
}
```

The three candidates sat **0.00 m, 20.07 m, 34.99 m** from the requested point.

### Worth knowing

**`distance` is the snap distance and it is your data-quality signal.** Zero
means the coordinate was already on a road. Tens of metres is normal for a
rooftop geocode. Hundreds of metres means the plan will serve a road, not your
customer.

**A coordinate outside the extract still snaps, and still routes.** A point at
`-87.5, 12.9` -- in Nicaragua, well outside this Costa Rica graph -- snapped
**276 km** to `[-85.638071, 11.206595]` and then routed
successfully with a `200`. There is no "too far" rejection at the engine level.
If your input can contain a bad coordinate, check the snap distance yourself or
bound it with `radiuses`; nothing downstream will do it for you.

---

## `POST /v1/nearest/batch`

**Use case.** The same check across a whole address book in one call, which is
how you would actually audit a customer master or validate an import.

### Parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `coordinates` | array of Coordinate | **yes** | Up to `NEAREST_MAX_COORDINATES` (default 1,000) |
| `number` | integer >= 1 | no | Candidates per coordinate |
| `profile` | Profile | no | |

### Real example

```bash
curl -s http://localhost:8000/v1/nearest/batch \
  -H 'content-type: application/json' \
  -d '{"coordinates": [{"longitude": -84.088218, "latitude": 9.909005}, {"longitude": -84.07515, "latitude": 9.939902}, {"longitude": -84.055146, "latitude": 9.935129}, {"longitude": -84.055441, "latitude": 9.912299}, {"longitude": -84.059088, "latitude": 9.932245}]}'
```

### Real result

`200` · **0.7 ms** · 1,286 bytes

```json
{
  "code": "Ok",
  "results": [
    {
      "code": "Ok",
      "waypoints": [
        {
          "nodes": [
            2651371814,
            3495976948
          ],
          "hint": "<opaque snap hint, elided>",
          "name": "Avenida 56",
          "distance": 0,
          "location": [
            -84.088218,
            9.909005
          ]
        }
      ]
    },
    "... 4 more entries elided ..."
  ]
}
```

The first drop snapped to **Avenida 56** at
0.00 m.

### Worth knowing

**One request becomes one engine call per coordinate**, fanned out with
`NEAREST_BATCH_CONCURRENCY` (default 8). That is why its rate limit is
`60/minute` against `/nearest`'s `600/minute`: at the 1,000-coordinate ceiling,
sixty requests a minute is already 60,000 upstream snaps.

**`results` preserves input order**, and each entry carries its own `code`, so a
single bad coordinate does not fail the batch.

---

## `POST /v1/vrp`

**Use case.** The whole day in one call: assign every drop to a depot, split
each depot's work into vehicle loads, and sequence each vehicle. This is the
endpoint the dispatch screen calls.

Read [`docs/tier2/40-what-the-vrp-does.md`](40-what-the-vrp-does.md) before
designing against it -- in particular, `capacity` is a **stop count** and
`vehicle_count` is never read.

### Parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `depots` | array of Stop | **yes** | 1 to 500. An `id` becomes the vehicle label |
| `stops` | array of Stop | **yes** | 1 to `VRP_MAX_STOPS` (2,000) |
| `capacity` | integer 1..10000 | no (35) | **Maximum stops per vehicle.** Not weight or volume |
| `clustering_mode` | `travel_time` \| `distance` \| `radial` | no (`travel_time`) | Which cost picks the depot |
| `hysteresis_m` | number >= 0 | no (2000) | How much better a non-nearest depot must be |
| `max_radius_km` | number > 0 | no | Beyond this from its depot, a stop is unreachable |
| `roundtrip` | bool | no (true) | Return to depot |
| `vehicle_count` | integer > 0 | no | **Validated and ignored.** Vehicle count is an output |

A `Stop` is `{longitude, latitude, id?}`.

### Real example

Two real depots -- Guadalupe and Grecia -- and 40 real GAM drops, 15 stops per
vehicle:

```json
{
  "depots": [
    {
      "id": "GUADALUPE",
      "longitude": -84.0531,
      "latitude": 9.9472
    },
    {
      "id": "GRECIA",
      "longitude": -84.3121,
      "latitude": 10.0734
    }
  ],
  "stops": [
    {
      "id": "ORD-000005",
      "longitude": -84.02466,
      "latitude": 9.894413
    },
    {
      "id": "ORD-000008",
      "longitude": -84.088218,
      "latitude": 9.909005
    },
    {
      "id": "ORD-000009",
      "longitude": -84.130023,
      "latitude": 10.010431
    },
    "... 37 more real drops elided ..."
  ],
  "capacity": 15,
  "clustering_mode": "travel_time",
  "roundtrip": true
}
```

### Real result

`200` · **12.6 ms** · 163,497 bytes · 4 routes

```json
{
  "code": "Ok",
  "routes": [ … 4 routes, summarised below … ],
  "total_distance": 238123.8,
  "total_duration": 24260.5
}
```

| Vehicle | Depot | Stops | Distance | Duration |
|---|---|---|---|---|
| `GUADALUPE-1` | 0 | 15 | 84,573 m | 131 min |
| `GUADALUPE-2` | 0 | 15 | 73,276 m | 122 min |
| `GUADALUPE-3` | 0 | 9 | 43,425 m | 79 min |
| `GRECIA` | 1 | 1 | 36,850 m | 72 min |
| **Total** | | **40** | **238.1 km** | **6.7 h** |

One route, in full, with its geometry elided:

```json
{
  "vehicle_id": "GRECIA",
  "depot_index": 1,
  "stops_indices": [
    29
  ],
  "stop_ids": [
    "ORD-000073"
  ],
  "stop_coordinates": [
    {
      "longitude": -84.216778,
      "latitude": 10.042147
    }
  ],
  "route_geometry": {
    "coordinates": [
      [
        -84.312303,
        10.073361
      ],
      [
        -84.312256,
        10.073123
      ],
      "... 1500 more coordinate pairs elided ..."
    ],
    "type": "LineString"
  },
  "distance_meters": 36849.7,
  "duration_seconds": 4296.1
}
```

### Worth knowing

**Vehicle count fell out of the data.** Nobody asked for four vehicles. 39 drops
landed on Guadalupe, which at 15 per vehicle is three; one drop was closer to
Grecia, which is the fourth. That single-stop Grecia route driving
37 km is the honest output of a
capacity-and-geography model with no notion of whether the trip is worth making.

**`stops_indices` indexes your request array**, and `stop_ids` echoes your own
identifiers when you supplied them. Use the ids; index arithmetic across a
40-stop request is how stops get delivered to the wrong address.

**There is no `unreachable_stops` field here.** That is deliberate
compatibility. To see what was dropped, call `/v1/vrp/allocate` with the same
body.

---

## `POST /v1/vrp/allocate`

**Use case.** Territories without paying for the routing. "Which warehouse
serves this customer?" -- for network design, for checking catchments after a
depot moves, and as the cheap pre-flight before committing to a full `/vrp`.

### Parameters

Identical to `/v1/vrp`. `capacity` and `roundtrip` are accepted and have no
effect here, since nothing is sequenced.

### Real example

All six real depots against 120 real nationwide drops, with a 60 km limit:

```json
{
  "depots": [
    {
      "id": "Guadalupe (San Jose)",
      "longitude": -84.0531,
      "latitude": 9.9472
    },
    {
      "id": "Grecia (Alajuela)",
      "longitude": -84.3121,
      "latitude": 10.0734
    },
    "... 4 more real depots elided ..."
  ],
  "stops": [
    {
      "id": "ORD-000001",
      "longitude": -84.225307,
      "latitude": 10.007028
    },
    {
      "id": "ORD-000002",
      "longitude": -84.230922,
      "latitude": 10.005997
    },
    "... 118 more drops elided ..."
  ],
  "capacity": 30,
  "clustering_mode": "travel_time",
  "max_radius_km": 60
}
```

### Real result

`200` · **1.9 ms** · 1,771 bytes

```json
{
  "code": "Ok",
  "allocations": { "<depot id>": ["<stop id>", …], … },
  "unreachable_stops": ["ORD-000007", "ORD-000020", "ORD-000049", "ORD-000051", … ]
}
```

| Depot | Stops assigned |
|---|---|
| Guadalupe (San Jose) | 76 |
| Grecia (Alajuela) | 16 |
| Guapiles (Limon) | 5 |
| San Carlos (Alajuela North) | 5 |
| Liberia (Guanacaste) | 3 |
| Perez Zeledon (San Jose South) | 2 |
| **Unreachable** | **13** |

### Worth knowing

**13 of 120 drops came back unreachable**, every one of them
because no depot was within the 60 km road limit -- this corpus is nationwide
and the depot network is not. Drop `max_radius_km` and they are all served, some
of them absurdly. The limit is how you make "we do not serve this address" an
explicit answer instead of a 300 km route.

**This is the endpoint to run first.** It is an order of magnitude cheaper than
`/vrp` -- 1.9 ms against 12.6 ms here -- because it stops after the
matrix and the allocation. Bad geocodes, unservable addresses and surprising
catchments all surface here, before anyone waits on a full solve.

---

## `GET /v1/tile/{profile}/{z}/{x}/{y}.mvt`

**Use case.** Draw the routing graph itself -- the edges the router can actually
use, with their speeds and weights. This is a debugging and visualisation
surface: when a plan takes a road nobody expects, this shows you the graph it
was planning on.

### Parameters

All in the path. There is no request body.

| Segment | Type | Notes |
|---|---|---|
| `profile` | `driving` \| `cycling` \| `walking` | Which graph |
| `z` | integer | Zoom. The engine only produces data around **z12-z16**; outside that you get an empty tile |
| `x`, `y` | integer | Slippy-map tile coordinates |
| `.mvt` | literal | **Part of the path.** Without it you get a 404 |

### Real example

The z13 tile covering the Guadalupe depot:

```bash
curl -s -o sanjose.mvt http://localhost:8000/v1/tile/driving/13/2183/3868.mvt
```

### Real result

`200` · **28.8 ms** · 1,395,064 bytes · application/x-protobuf

Binary Mapbox Vector Tile. First bytes, hex:

```
1ad9f54478020a06737065656473288020122018020801220809941086350a0012121000000101020203030403050406
```

Feed it to any MVT renderer -- MapLibre, Mapbox GL, `tippecanoe`, QGIS. It is
protobuf, not JSON, so there is nothing human-readable to show here; the
1,395,064 bytes are the road geometry for that square.

### Worth knowing

**The `.mvt` suffix is load-bearing.** The same tile without it:

`404` · **1.0 ms** · 22 bytes -- `{"detail": "Not Found"}`

That is the documented behaviour, not a bug, but it catches everyone once.

---

## `GET /health` and `GET /ready`

**Use case.** `/health` answers "is this process alive" for a load balancer.
`/ready` answers "can it actually serve traffic", which additionally means the
engine is reachable. Point your liveness probe at the first and your readiness
probe and deploy gate at the second.

### Parameters

None.

### Real result

`GET /health` -- `200` · **16.6 ms** · 69 bytes

```json
{
  "status": "healthy",
  "service": "OSRM API Gateway",
  "osrm_backend": "up"
}
```

`GET /ready` -- `200` · **0.9 ms** · 67 bytes

```json
{
  "status": "ready",
  "service": "OSRM API Gateway",
  "osrm_backend": "up"
}
```

### Worth knowing

**`/ready` probes the engine**, which is why `make compose-health` and
`make jail-health` use it: one call covers gateway and engine together. It
routes between `HEALTH_CHECK_COORDS` with a `HEALTH_CHECK_TIMEOUT` (default 2 s)
budget, and that probe blocks the response -- keep the timeout well below the
Docker `HEALTHCHECK --timeout`, which is 8 s.

**Neither is rate limited**, so probing them aggressively is safe.

---

## `GET /metrics`

**Use case.** Prometheus scrape target. Served wherever `METRICS_ENDPOINT`
points, `/metrics` by default.

### Parameters

None.

### Real result

`200` · **0.7 ms** · 12,532 bytes · text/plain; version=0.0.4; charset=utf-8

Real lines from the capture run, after the requests above had been served:

```
cache_lookups_total{result="hit",service="nearest",tier="l1"} 5
cache_lookups_total{result="hit",service="table",tier="l1"} 2
cache_lookups_total{result="hit",service="trip",tier="l1"} 4
cache_lookups_total{result="miss",service="match",tier="l1"} 1
cache_lookups_total{result="miss",service="nearest",tier="l1"} 6
cache_lookups_total{result="miss",service="route",tier="l1"} 4
```

### Worth knowing

**The metric names match `prometheus-fastapi-instrumentator`** -- names, types,
labels and bucket boundaries -- so dashboards written against the retired Python
gateway keep working unchanged.

**`status` is grouped**, as `2xx`/`4xx`/`5xx` rather than the raw code. An
unrouted path is labelled `handler="none"`, and tiles collapse to a single
handler label so one client cannot explode cardinality by walking a zoom level.

**`process_*` metrics need `/proc`.** They appear under Docker and in CI, and
**not** in the FreeBSD jail or on macOS. Dashboards that assume them will have
blank panels on the jail deployment.

---

## `GET /docs`, `/redoc` and `/openapi.json`

**Use case.** The contract, browsable and machine-readable. `/docs` is Swagger
UI, `/redoc` is ReDoc, and `/openapi.json` is the document both render.

### Parameters

None.

### Real result

`GET /openapi.json` -- `200` · **3.4 ms** · 53,946 bytes describing **23 paths** and
**28 schemas**.

`GET /docs` -- `200` · **0.4 ms** · 426 bytes · text/html; charset=utf-8

### Worth knowing

**The document is generated from the types that serialise the responses**, not
from a second description of them, so it cannot drift from the implementation.
It is also the fastest way to settle an argument about a field: every parameter
table in this guide was checked against this document, and four of my own first
attempts at these examples were rejected as `422` because I had guessed the
shape instead of reading it.

**It declares no authentication**, and a test enforces that. The gateway expects
to sit behind something that authenticates -- see
[`docs/golive/GO_LIVE_PLAN.md`](../golive/GO_LIVE_PLAN.md).

---

## Errors, as they really come back

Every refusal names what it refused and why. The shape is pydantic's, kept
deliberately so clients written against the retired Python gateway still parse.

### `422` -- the request is wrong

A longitude of -184:

```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": [
        "body",
        "origin",
        "longitude"
      ],
      "msg": "Input should be greater than or equal to -180",
      "input": -184.0,
      "ctx": {
        "ge": -180.0
      }
    }
  ]
}
```

`loc` walks to the exact field. A client can branch on `type` -- here
`greater_than_equal` -- and `ctx` carries the bound that was crossed.

### `422` -- the request is too big

A 120x120 matrix, shown earlier, comes back as a `value_error` at the body level
naming the cell count and the limit. Same for a `/vrp` over `VRP_MAX_STOPS` and a
`/match` trace over `OSRM_MAX_URL_BYTES`. **These are fix-the-request errors, not
retry errors.**

### `429` -- too many requests

Real, from hitting `/v1/vrp` repeatedly: the 101th
call in under a minute was refused, exactly at the configured `100/minute`.

```json
{
  "error": "Rate limit exceeded: 100 per 1 minute"
}
```

Per-endpoint and per-client-address. Behind an L7 proxy without
`FORWARDED_ALLOW_IPS`, every client shares one bucket -- see
[`docs/features/rate_limiting.md`](../features/rate_limiting.md).

### `503` -- the solve queue is full

`/vrp` admits `VRP_MAX_CONCURRENCY` solves per worker. Past that, requests wait
up to `VRP_QUEUE_TIMEOUT` and are then refused with a `Retry-After` header. This
one is worth retrying, after the delay it names.

### Rate limits in force

| Endpoint | Default |
|---|---|
| `/route`, `/match`, `/nearest`, `/tile` | `600/minute` |
| `/matrix`, `/matrix-graph`, `/trip` | `300/minute` |
| `/vrp`, `/vrp/allocate` | `100/minute` |
| `/nearest/batch` | `60/minute` |
| `/health`, `/ready`, `/metrics` | unlimited |

---

## Reproducing this

Everything above came from a local run. To repeat it:

```bash
# 1. build a Costa Rica graph with a local OSRM toolchain
osrm-extract -p /opt/homebrew/share/osrm/profiles/car.lua data/costa-rica-latest.osm.pbf
osrm-partition data/costa-rica-latest.osrm
osrm-customize data/costa-rica-latest.osrm

# 2. run the engine
osrm-routed --algorithm mld data/costa-rica-latest.osrm --port 5000 --max-table-size 100

# 3. run the gateway against it
cargo build --manifest-path gateway/Cargo.toml
OSRM_BASE_URL=http://127.0.0.1:5000 HOST=127.0.0.1 PORT=8000 \
  ./gateway/target/debug/osrm-api-gateway
```

Coordinates came from `data/deliveries_cr.json`. For a deployed instance instead
of a local one, see [`docs/RUNBOOK.md`](../RUNBOOK.md).

---

*Captured 2026-09-12 against a live gateway. Request and response bodies are the
real bytes; long geometries are elided with the count of what was removed.
Timings are from a warm local engine on a development machine and are indicative
only.*
