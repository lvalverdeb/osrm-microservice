# API Reference - OSRM Backend Microservice

This document provides a detailed reference for developers interacting with the OSRM Backend Microservice.

## Base URL

The service runs by default on port `8000` (mapped to `8080` in Docker).

- **Local**: `http://localhost:8000`
- **Docker**: `http://localhost:8080`

---

## Data Models (Schemas)

The following Pydantic models define the structure of requests and responses.

### `Coordinate`

Standard representation of a geographic point.

| Field | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude of the point in decimal degrees. |
| `latitude` | `float` | Latitude of the point in decimal degrees. |

### `CommonRoutingOptions`

Optional OSRM general options applicable to Route, Table, Match, and Trip services.

| Field | Type | Description |
| :--- | :--- | :--- |
| `bearings` | `List[str]` | Per-coordinate bearing constraints as 'angle,deviation' strings (e.g. '90,30'). |
| `radiuses` | `List[float]` | Per-coordinate snapping radius in meters. Use `null` for unlimited. |
| `hints` | `List[str]` | Per-coordinate hint strings from a previous OSRM response. |
| `approaches` | `List[str]` | Per-coordinate approach side: `unrestricted` or `curb`. |
| `exclude` | `List[str]` | Road classes to exclude globally (e.g. `['motorway', 'toll']`). |
| `snapping` | `str` | Edge selection: `default` or `any`. |
| `skip_waypoints` | `bool` | Suppress the waypoints array in the response. |

### `RouteRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `origin` | `Coordinate` | Starting point of the route. |
| `destination` | `Coordinate` | Final destination point. |
| `waypoints` | `List[Coordinate]` | Optional intermediate points to pass through. |
| `profile` | `str` | Routing profile: `driving` (default), `cycling`, `walking`. |
| `alternatives` | `bool or int` | Return alternates (boolean) or a specific number (integer). |
| `overview` | `str` | Geometry resolution: `simplified`, `full` (default), `false`. |
| `geometries` | `str` | Geometry format: `polyline`, `polyline6`, `geojson` (default). |
| `steps` | `bool` | Return step-by-step turn instructions (Default: `true`). |
| `annotations` | `str` | Comma-separated metadata per segment (e.g. `distance,duration`). |

### `MatrixRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | List of points to include in the calculation. |
| `profile` | `str` | Routing profile: `driving`, `cycling`, `walking`. |
| `sources` | `List[int]` | Indices of points to use as origins. |
| `destinations` | `List[int]` | Indices of points to use as destinations. |
| `annotations` | `str` | `duration`, `distance`, or `duration,distance` (default). |

### `MatchRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `breadcrumbs` | `List[GPSBreadcrumb]` | Sequence of points to snap to the road network. |
| `profile` | `str` | Routing profile: `driving`, `cycling`, `walking`. |
| `overview` | `str` | Geometry resolution: `simplified`, `full`, `false`. |
| `geometries` | `str` | Geometry format: `polyline`, `polyline6`, `geojson`. |
| `steps` | `bool` | Return steps for the matched path. |
| `annotations` | `str` | Comma-separated metadata per segment. |
| `gaps` | `str` | Split trace on large gaps: `split` or `ignore`. |
| `tidy` | `bool` | Remove repeated or out-of-order coordinates before matching. |
| `match_waypoints` | `List[int]` | Indices into breadcrumbs to treat as explicit waypoints. |

### `GPSBreadcrumb`

A single GPS trace point.

| Field | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude of the point. |
| `latitude` | `float` | Latitude of the point. |
| `timestamp` | `int` | Unix timestamp. |
| `accuracy_meters` | `float` | Snapping radius/accuracy in meters (Default: `5.0`). |

### `Stop` (Inherits from `Coordinate`)

A geographic delivery stop or depot location with identification.

| Field | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude of the point. |
| `latitude` | `float` | Latitude of the point. |
| `id` | `str or int` | Optional unique identifier for tracking. |

### `TripRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | Coordinates to optimize. |
| `roundtrip` | `bool` | Return to first point at the end (Default: `true`). |
| `source` | `str` | Start point restriction: `first` or `any`. |
| `destination` | `str` | End point restriction: `last` or `any`. |
| `profile` | `str` | Routing profile: `driving`, `cycling`, `walking`. |
| `overview` | `str` | Geometry resolution: `simplified`, `full`, `false`. |
| `geometries` | `str` | Geometry format: `polyline`, `polyline6`, `geojson`. |
| `steps` | `bool` | Return turn-by-turn steps. |
| `annotations` | `str` | Comma-separated segment metadata. |

### `NearestRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `coordinate` | `Coordinate` | Point to snap to the network. |
| `number` | `int` | Number of nearest road segments to return (Default: 1). |
| `profile` | `str` | Routing profile: `driving`, `cycling`, `walking`. |

### `NearestResponse`

| Field | Type | Description |
| :--- | :--- | :--- |
| `code` | `str` | Operation status code (e.g., `Ok`). |
| `waypoints` | `List[Dict]` | Snapped road segments metadata. |

### `VrpRequest`

| Field | Type | Description |
| :--- | :--- | :--- |
| `depots` | `List[Stop]` | List of warehouse/depot locations. |
| `stops` | `List[Stop]` | List of delivery stops. |
| `vehicle_count` | `int` | Number of available vehicles. Defaults to one per depot. |
| `capacity` | `int` | Maximum stops/packages per vehicle (Default: 35). |
| `max_radius_km` | `float` | Optional maximum road distance from depot (km). |
| `clustering_mode` | `str` | Clustering type: `travel_time` (default), `distance`, or `radial`. |
| `hysteresis_m` | `float` | Depot snapping boundary tolerance in meters (Default: `2000.0`). |
| `roundtrip` | `bool` | Return to depot at route end (Default: `true`). |

### `VehicleRoute`

| Field | Type | Description |
| :--- | :--- | :--- |
| `vehicle_id` | `str or int` | Suffix-labelled identifier of the vehicle. |
| `depot_index` | `int` | Index of the assigned warehouse. |
| `stops_indices` | `List[int]` | Optimized sequence of stop indices. |
| `stop_ids` | `List[str or int]` | Optional list of stop IDs in optimized order. |
| `stop_coordinates` | `List[Coordinate]` | Coordinates in optimized order. |
| `route_geometry` | `Dict` | GeoJSON LineString geometry of the route. |
| `distance_meters` | `float` | Total distance in meters. |
| `duration_seconds` | `float` | Total duration in seconds. |

### `VrpResponse`

| Field | Type | Description |
| :--- | :--- | :--- |
| `code` | `str` | Response status code. |
| `routes` | `List[VehicleRoute]` | Optimized routes per vehicle. |
| `total_distance` | `float` | Total distance across all vehicles. |
| `total_duration` | `float` | Total travel time across all vehicles. |

### `VrpAllocationResponse`

| Field | Type | Description |
| :--- | :--- | :--- |
| `code` | `str` | Response status code. |
| `allocations` | `Dict[str/int, List]` | Depot identifiers mapping to assigned stop IDs/indices. |
| `unreachable_stops` | `List` | List of stop IDs/indices exceeding route limits. |

---

## Endpoints

### System Endpoints

#### `GET /health`

Checks if the gateway is running and probes the OSRM backend.

**Response Body:**
```json
{
  "status": "healthy",
  "service": "OSRM API Gateway",
  "osrm_backend": "up"
}
```

`status` is `"degraded"` and `osrm_backend` is `"down"` when the OSRM backend probe fails.
This endpoint always returns **200**, degraded or not — use `GET /ready` for load balancers.

#### `GET /ready`

Readiness probe for load balancers and deploy-time checks. Same OSRM probe as
`/health`, but the HTTP status carries the verdict: **200** when the backend is
up, **503** when it is down, so a balancer drains the node instead of routing to
an engine that cannot answer.

**Response Body:**
```json
{
  "status": "ready",
  "service": "OSRM API Gateway",
  "osrm_backend": "up"
}
```

With HTTP 503, `status` is `"not_ready"` and `osrm_backend` is `"down"`.

---

### Routing Endpoints

#### `POST /route`

Calculates the fastest route between coordinates.

**Request Body (`RouteRequest`):**
```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "profile": "walking",
  "steps": true
}
```

**Response Body (JSON):** Passes through the OSRM `/route` output containing `code`, `routes`, and `waypoints`.

---

#### `POST /nearest`

Snaps a coordinate to the nearest road segments.

**Request Body (`NearestRequest`):**
```json
{
  "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
  "number": 1,
  "profile": "driving"
}
```

**Response Body (`NearestResponse`):**
```json
{
  "code": "Ok",
  "waypoints": [
    {
      "name": "Calle Central",
      "distance": 4.2,
      "location": [-84.0906, 9.9282],
      "hint": "abc..."
    }
  ]
}
```

---

### Matrix Endpoints

#### `POST /matrix`

Calculates travel times/distances between all supplied locations.

**Request Body (`MatrixRequest`):**
```json
{
  "coordinates": [
    {"longitude": -84.0907, "latitude": 9.9281},
    {"longitude": -84.0833, "latitude": 9.9333}
  ],
  "profile": "driving"
}
```

**Response Body:** Passes through the OSRM `/table` output containing `code`, `durations`, `distances`, `sources`, and `destinations`.

**Size limit (applies to `/matrix` and `/matrix-graph`):** the engine charges
`sources x destinations` cells, not coordinates, and refuses anything above
`MATRIX_MAX_CELLS` (default **10,000**). Requests past it get a 422 naming the
limit rather than an opaque upstream 400.

| Request | Cells | Result |
|---------|-------|--------|
| 100 coordinates, no `sources`/`destinations` | 10,000 | accepted |
| 101 coordinates, no `sources`/`destinations` | 10,201 | 422 |
| 4 `sources` x 2500 `destinations` (2504 coordinates) | 10,000 | accepted |

Omitting `sources` or `destinations` means "all coordinates", so a symmetric
matrix is capped at 100x100 while an asymmetric one may carry far more
coordinates. Split larger jobs, or narrow them with `sources`/`destinations`.

---

#### `POST /matrix-graph`

Builds a serializable directed graph representation of the matrix.

**Request Body (`MatrixRequest`):** Same as `POST /matrix`.

**Response Body (`MatrixGraphResponse`):**
```json
{
  "nodes": [{"id": 0, "lon": -84.0907, "lat": 9.9281}],
  "edges": [{"source": 0, "target": 1, "duration": 180.0, "distance": 1200.0}]
}
```

---

### Map Matching Endpoints

#### `POST /match`

Snaps noisy GPS points to the road network.

**Request Body (`MatchRequest`):**
```json
{
  "breadcrumbs": [
    {"longitude": -84.0907, "latitude": 9.9281, "timestamp": 1713000000},
    {"longitude": -84.0880, "latitude": 9.9300, "timestamp": 1713000030}
  ],
  "profile": "driving",
  "tidy": true
}
```

**Response Body:** Passes through the OSRM `/match` output containing `code`, `matchings`, and `tracepoints`.

---

### Optimization Endpoints

#### `POST /trip`

Optimizes a sequence of stops (Travelling Salesperson Problem).

**Request Body (`TripRequest`):**
```json
{
  "coordinates": [
    {"longitude": -84.0907, "latitude": 9.9281},
    {"longitude": -84.0833, "latitude": 9.9333},
    {"longitude": -84.1000, "latitude": 9.9400}
  ],
  "roundtrip": true,
  "profile": "driving"
}
```

**Response Body:** Passes through the OSRM `/trip` output containing `code`, `trips`, and `waypoints`.

---

#### `POST /vrp`

Solves multi-vehicle Vehicle Routing Problems using location-allocation clustering.

**Request Body (`VrpRequest`):**
```json
{
  "depots": [{"id": "D1", "longitude": -84.09, "latitude": 9.93}],
  "stops": [
    {"id": "S1", "longitude": -84.10, "latitude": 9.94},
    {"id": "S2", "longitude": -84.14, "latitude": 9.96}
  ],
  "vehicle_count": 2,
  "capacity": 35
}
```

**Response Body (`VrpResponse`):**
```json
{
  "code": "Ok",
  "routes": [
    {
      "vehicle_id": "D1-1",
      "depot_index": 0,
      "stops_indices": [0, 1],
      "stop_ids": ["S1", "S2"],
      "stop_coordinates": [
        {"longitude": -84.10, "latitude": 9.94},
        {"longitude": -84.14, "latitude": 9.96}
      ],
      "route_geometry": {
        "type": "LineString",
        "coordinates": [[-84.09, 9.93], [-84.10, 9.94], [-84.14, 9.96], [-84.09, 9.93]]
      },
      "distance_meters": 12450.0,
      "duration_seconds": 920.0
    }
  ],
  "total_distance": 12450.0,
  "total_duration": 920.0
}
```

**Capacity limits (both `/vrp` and `/vrp/allocate`):**

| Status | Cause | Tuned by |
|--------|-------|----------|
| `422` | More than `VRP_MAX_STOPS` stops in one request (default 2000). | `VRP_MAX_STOPS` |
| `503` | No solve slot free within `VRP_QUEUE_TIMEOUT` seconds. Includes a `Retry-After` header. | `VRP_MAX_CONCURRENCY`, `VRP_QUEUE_TIMEOUT` |

Peak memory is stops x concurrent solves, so both bounds matter: a 2000-stop
solve peaks near 277 MB. `VRP_MAX_CONCURRENCY` applies per worker process, so a
node admits `WORKERS x VRP_MAX_CONCURRENCY` solves at once. See
[configuration.md](configuration.md#vrp--matrix-tuning).

---

#### `POST /vrp/allocate`

Pre-clusters stops to depots before routing (ideal for checking assignments).

**Request Body (`VrpRequest`):** Same as `POST /vrp`.

**Response Body (`VrpAllocationResponse`):**
```json
{
  "code": "Ok",
  "allocations": {
    "D1": ["S1", "S2"]
  },
  "unreachable_stops": []
}
```

---

### Tiles Endpoints

#### `GET /tile/{profile}/{z}/{x}/{y}.mvt`

Proxy Mapbox Vector Tiles from the OSRM backend. Minimum zoom level: 12.

---

## Rate Limits

All endpoints are rate-limited per client IP. `GET /health`, `GET /ready`, and `GET /metrics` are unlimited.

| Method | Path | Limit | Env Variable |
|--------|------|-------|-------------|
| POST | `/route` | 600/min | `RATE_LIMIT_ROUTE` |
| POST | `/matrix` | 300/min | `RATE_LIMIT_MATRIX` |
| POST | `/matrix-graph` | 300/min | `RATE_LIMIT_MATRIX` |
| POST | `/match` | 600/min | `RATE_LIMIT_MATCH` |
| POST | `/trip` | 300/min | `RATE_LIMIT_TRIP` |
| POST | `/nearest` | 600/min | `RATE_LIMIT_NEAREST` |
| GET | `/tile/{p}/{z}/{x}/{y}.mvt` | 600/min | `RATE_LIMIT_TILE` |
| POST | `/vrp` | 100/min | `RATE_LIMIT_VRP` |
| POST | `/vrp/allocate` | 100/min | `RATE_LIMIT_VRP` |

Exceeding a limit returns HTTP `429 Too Many Requests`.

---

## Error Handling

The service returns structured OSRM error bodies when available:

```json
{
  "detail": {
    "code": "NoRoute",
    "message": "Could not find a route between coordinates"
  }
}
```
