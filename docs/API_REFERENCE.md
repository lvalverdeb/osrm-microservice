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
| `overview` | `str` | Geometry resolution: `simplified` (default), `full`, `false`. |
| `geometries` | `str` | Geometry format: `polyline` (default), `polyline6`, `geojson`. |
| `steps` | `bool` | Return step-by-step turn instructions (Default: `true`). |
| `annotations` | `str` | Comma-separated metadata per segment (e.g. `distance,duration`). |

### `MatrixRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | List of points to include in the calculation. |
| `profile` | `str` | Routing profile: `driving`, `cycling`, `walking`. |
| `sources` | `List[int]` | Indices of points to use as origins. |
| `destinations` | `List[int]` | Indices of points to use as destinations. |
| `annotations` | `str` | `duration` (default), `distance`, or `duration,distance`. |

### `MatchRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `breadcrumbs` | `List[GPSBreadcrumb]` | Sequence of points to snap to the road network. |
| `profile` | `str` | Routing profile: `driving`, `cycling`, `walking`. |
| `steps` | `bool` | Return steps for the matched path. |
| `tidy` | `bool` | Remove repeated or out-of-order coordinates before matching. |

### `NearestRequest` (Inherits from `CommonRoutingOptions`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `coordinate` | `Coordinate` | Point to snap to the network. |
| `number` | `int` | Number of nearest road segments to return (Default: 1). |
| `profile` | `str` | Routing profile: `driving`, `cycling`, `walking`. |

---

## Endpoints

### 1. Routing

#### `POST /route`

Calculates the fastest route between points.

**Request Body (`RouteRequest`):**

```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "profile": "walking",
  "steps": true
}
```

---

### 2. Matrix (Distance Tables)

#### `POST /matrix`
#### `POST /matrix-graph`

Generates a table of durations and distances.

---

### 3. Map Matching

#### `POST /match`

Snaps noisy GPS traces to the road network.

---

### 4. Optimization (TSP)

#### `POST /trip`

Solves the Traveling Salesperson Problem.

---

### 5. Nearest (Road Snapping)

#### `POST /nearest`

Find the nearest road network point(s) to a given coordinate.

**Request Body (`NearestRequest`):**

```json
{
  "coordinate": {"longitude": -84.09, "latitude": 9.93},
  "number": 3,
  "profile": "cycling"
}
```

---

### 6. Tiles (Mapbox Vector Tiles)

#### `GET /tile/{profile}/{z}/{x}/{y}.mvt`

Proxy a Mapbox Vector Tile from OSRM. Minimum zoom level: 12.

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
