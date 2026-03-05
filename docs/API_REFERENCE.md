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

### `GPSBreadcrumb`

Represents a single point in a GPS trace for map matching.

| Field | Type | Description |
| :--- | :--- | :--- |
| `longitude` | `float` | Longitude in decimal degrees. |
| `latitude` | `float` | Latitude in decimal degrees. |
| `timestamp` | `int` | Unix timestamp (integer seconds). |
| `accuracy_meters` | `float` | Accuracy in meters (Default: 5.0). |

### `RouteRequest`

| Field | Type | Description |
| :--- | :--- | :--- |
| `origin` | `Coordinate` | Starting point of the route. |
| `destination` | `Coordinate` | Final destination point. |
| `waypoints` | `List[Coordinate]` | Optional intermediate points to pass through. |

### `MatchRequest`

| Field | Type | Description |
| :--- | :--- | :--- |
| `breadcrumbs` | `List[GPSBreadcrumb]` | Sequence of points to snap to the road network. |

### `MatrixRequest`

| Field | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | List of points to include in the calculation. |
| `sources` | `List[int]` | Indices of points to use as origins. |
| `destinations` | `List[int]` | Indices of points to use as destinations. |

---

## Endpoints

### 1. Routing

#### `POST /route`

Calculates the fastest driving route between an origin and destination, with optional intermediate waypoints.

**Request Body (`RouteRequest`):**

```json
{
  "origin": {"longitude": -84.09, "latitude": 9.93},
  "destination": {"longitude": -84.15, "latitude": 9.97},
  "waypoints": []
}
```

**Example Response:**

```json
{
  "code": "Ok",
  "routes": [
    {
      "geometry": {
        "type": "LineString",
        "coordinates": [[-84.09, 9.93], [-84.15, 9.97]]
      },
      "legs": [
        {
          "steps": [],
          "distance": 12500.5,
          "duration": 945.2,
          "summary": "Route Summary",
          "weight": 945.2
        }
      ],
      "distance": 12500.5,
      "duration": 945.2,
      "weight_name": "routability",
      "weight": 945.2
    }
  ],
  "waypoints": [
    {"name": "Origin", "location": [-84.09, 9.93]},
    {"name": "Destination", "location": [-84.15, 9.97]}
  ]
}
```

---

### 2. Matrix (Distance Tables)

#### `POST /matrix`

Generates a table of durations and distances between all provided coordinates.

**Request Body (`MatrixRequest`):**

| Field | Type | Description |
| :--- | :--- | :--- |
| `coordinates` | `List[Coordinate]` | List of points to include in the matrix. |
| `sources` | `Optional[List[int]]` | Indices into the coordinates list to use as origins. |
| `destinations` | `Optional[List[int]]` | Indices into the coordinates list to use as destinations. |

**Example:**

```json
{
  "coordinates": [
    {"longitude": -84.09, "latitude": 9.93},
    {"longitude": -84.15, "latitude": 9.97}
  ],
  "sources": [0],
  "destinations": [1]
}
```

**Example Response:**

```json
{
  "code": "Ok",
  "durations": [[0, 945.2], [940.1, 0]],
  "distances": [[0, 12500.5], [12450.2, 0]],
  "sources": [{"location": [-84.09, 9.93]}],
  "destinations": [{"location": [-84.15, 9.97]}]
}
```

#### `POST /matrix-graph`

Generates a distance/duration matrix and converts it into a serializable graph format (Nodes & Edges) compatible with NetworkX and other graph libraries.

**Request Body (`MatrixRequest`):**
Same as `POST /matrix`.

**Response Format:**
Returns a JSON object representing a directed graph:

- `nodes`: List of coordinate nodes with metadata.
- `edges`: List of weighted edges (distance/duration) between nodes.

**Example Response:**

```json
{
  "nodes": [
    {"id": 0, "longitude": -84.09, "latitude": 9.93},
    {"id": 1, "longitude": -84.15, "latitude": 9.97}
  ],
  "edges": [
    {"source": 0, "target": 1, "distance": 12500.5, "duration": 945.2},
    {"source": 1, "target": 0, "distance": 12450.2, "duration": 940.1}
  ]
}
```

---

### 3. Map Matching

#### `POST /match`

Snaps noisy GPS traces to the road network. Handles trace splitting if signal is lost.

**Request Body (`MatchRequest`):**

```json
{
  "breadcrumbs": [
    {"longitude": -84.09, "latitude": 9.93, "timestamp": 1741185000},
    {"longitude": -84.091, "latitude": 9.931, "timestamp": 1741185010}
  ]
}
```

**Example Response:**

```json
{
  "code": "Ok",
  "matchings": [
    {
      "confidence": 0.95,
      "geometry": {
        "type": "LineString",
        "coordinates": [[-84.09, 9.93], [-84.091, 9.931]]
      },
      "distance": 150.2,
      "duration": 12.5
    }
  ],
  "tracepoints": [
    {"matchings_index": 0, "waypoint_index": 0, "location": [-84.09, 9.93]},
    {"matchings_index": 0, "waypoint_index": 1, "location": [-84.091, 9.931]}
  ]
}
```

---

### 4. System

#### `GET /health`

Returns service status and metadata.

**Response:**

```json
{
  "status": "healthy",
  "service": "osrm-microservice"
}
```

---

## Error Handling

All endpoints return standard HTTP error codes:

- **422 Unprocessable Entity**: Invalid request schema (Pydantic validation).
- **500 Internal Server Error**: Downstream OSRM API failure or internal logic error.
