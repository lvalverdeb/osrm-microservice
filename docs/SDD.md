# Software Design Description

## For OSRM API Gateway

**Version 0.3.0**  
Prepared by Luis Valverde  
lvalverdeb  
2026-06-25

## Table of Contents

- [1. Introduction](#1-introduction)
  - [1.1 Document Purpose](#11-document-purpose)
  - [1.2 Subject Scope](#12-subject-scope)
  - [1.3 Definitions, Acronyms, and Abbreviations](#13-definitions-acronyms-and-abbreviations)
  - [1.4 References](#14-references)
  - [1.5 Document Overview](#15-document-overview)
- [2. Design Overview](#2-design-overview)
  - [2.1 Stakeholder Concerns](#21-stakeholder-concerns)
  - [2.2 Selected Viewpoints](#22-selected-viewpoints)
- [3. Design Views](#3-design-views)
  - [3.1 Context View](#31-context-view)
  - [3.2 Composition View](#32-composition-view)
  - [3.3 Logical View](#33-logical-view)
  - [3.4 Information View](#34-information-view)
  - [3.5 Interface View](#35-interface-view)
  - [3.6 Interaction View](#36-interaction-view)
  - [3.7 Algorithm View](#37-algorithm-view)
  - [3.8 Deployment View](#38-deployment-view)
  - [3.9 Concurrency View](#39-concurrency-view)
  - [3.10 Patterns View](#310-patterns-view)
- [4. Decisions](#4-decisions)
- [5. Appendixes](#5-appendixes)
  - [5.1 VRP Mathematical Formulation](#51-vrp-mathematical-formulation)
  - [5.2 Rate Limiting Configuration](#52-rate-limiting-configuration)
  - [5.3 Redis Cache Configuration](#53-redis-cache-configuration)

---

## 1. Introduction

### 1.1 Document Purpose

This Software Design Description (SDD) defines the architecture and system design of the OSRM API Gateway (v0.2.2). It serves as the primary technical reference for developers, maintainers, and operators to understand how the system is structured, how components interact, and how design decisions map to functional requirements. The document describes both the preliminary (architectural) and detailed (component-level) design stages.

**Intended audiences:** Software engineers, DevOps engineers, technical architects, QA engineers, and future maintainers of the system.

### 1.2 Subject Scope

The OSRM API Gateway is a FastAPI-based asynchronous microservice that wraps the OSRM (Open Source Routing Machine) C++ backend, exposing specialized routing, map matching, optimization, and Vehicle Routing Problem (VRP) capabilities via a RESTful JSON API. Geographically focused on Costa Rica.

**Inclusions:**
- RESTful HTTP API with 10 endpoints
- Async HTTP proxy to OSRM backend with connection pooling
- Map-matched GPS trace processing
- Distance/duration matrix computation and graph conversion
- Traveling Salesperson Problem (TSP) optimization
- Vehicle Routing Problem (VRP) solver with Location-Allocation clustering
- Mapbox Vector Tile (MVT) proxy
- Rate limiting on all endpoints
- OpenTelemetry distributed tracing across all request paths
- Redis-backed distributed cache for route/matrix responses

**Exclusions:**
- OSRM C++ engine internals (data processing, routing algorithm)
- OSM data processing pipeline (handled by Dockerfile.builder)
- Client-side visualization (examples provided but outside scope)
- Authentication/authorization

### 1.3 Definitions, Acronyms, and Abbreviations

| Term | Definition |
|------|------------|
| API | Application Programming Interface |
| MLD | Multi-Level Dijkstra - OSRM's routing algorithm |
| MVT | Mapbox Vector Tile - binary tile format for map data |
| OSRM | Open Source Routing Machine - C++ routing engine |
| OTel | OpenTelemetry - observability framework for distributed tracing |
| Redis | Remote Dictionary Server - in-memory data structure store used as cache |
| SDD | Software Design Document |
| TSP | Traveling Salesperson Problem - route optimization for single vehicle |
| VRP | Vehicle Routing Problem - route optimization for multiple vehicles |
| Pydantic | Python data validation library using type annotations |
| FastAPI | Python async web framework for building APIs |
| httpx | Python async HTTP client |
| NetworkX | Python graph analysis library |
| Hysteresis | Buffer distance preventing assignment flipping near depot boundaries |
| Location-Allocation | Clustering algorithm assigning stops to optimal depots |
| Euclidean | Straight-line (crow-fly) distance between two points |
| W3C TraceContext | Standard for propagating trace context across service boundaries (traceparent header) |
| Cache-Aside | Application cache pattern: read from cache, on miss load from source and populate cache |

### 1.4 References

| Reference | Version | Type |
|-----------|---------|------|
| OSRM API v1 Documentation | v1.0 | Normative |
| FastAPI Documentation | 0.136.x | Normative |
| Pydantic v2 Documentation | 2.x | Normative |
| IEEE Std 1016-2009 (SDD) | 2009 | Informative |
| GitHub Spec Kit (SDD Methodology) | latest | Informative |
| GATEWAY_IMPLEMENTATION_PLAN.md | v0.2.2 | Normative |
| API_REFERENCE.md | v0.2.2 | Normative |
| docs/planning/vrp_proposal.md | v0.2.2 | Informative |
| REMEDIATION_PLAN_v2.md | v0.3.0 | Normative |

### 1.5 Document Overview

Section 2 presents the design overview, stakeholder concerns, and selected viewpoints. Section 3 contains the concrete design views (Context, Composition, Logical, Information, Interface, Interaction, Algorithm, Deployment, Concurrency, Patterns). Section 4 records significant architectural decisions. Section 5 contains appendixes with supplementary material.

---

## 2. Design Overview

### 2.1 Stakeholder Concerns

| Stakeholder | Concerns | Addressed By |
|-------------|----------|-------------|
| Application Developers | Component responsibilities, APIs, data models | Logical, Interface, Information views |
| DevOps/SRE | Deployment topology, scaling, health checks | Deployment, Concurrency views |
| QA Engineers | Testability, error handling, rate limits | Interaction, Interface views |
| Product Managers | Feature scope, VRP capabilities, API coverage | Context, Composition views |
| Future Maintainers | Design rationale, algorithmic decisions | Algorithm, Patterns, Decisions sections |

### 2.2 Selected Viewpoints

| Viewpoint | Concerns Addressed |
|-----------|-------------------|
| Context | System boundaries, external actors (Client, OSRM Backend) |
| Composition | Module decomposition (services, models, API layer) |
| Logical | Class hierarchy, type system, Pydantic model inheritance |
| Information | Data schemas, Pydantic models, request/response contracts |
| Interface | REST API specification, OSRM HTTP contract |
| Interaction | VRP data flow, async request patterns, error propagation |
| Algorithm | VRP Location-Allocation, TSP chunking, hysteresis logic |
| Deployment | Docker Compose topology, multi-stage builds, networking |
| Concurrency | Async I/O model, connection pooling, rate limiting |
| Patterns | Gateway pattern, Service pattern, Dependency Injection |

---

## 3. Design Views

### 3.1 Context View

**ID:** CTX-001  
**Title:** System Context  
**Viewpoint:** Context  
**Representation:**

```
┌─────────────┐     HTTP/JSON      ┌───────────────────────────────────┐
│   Client    │ ──────────────────> │    OSRM API Gateway (Port 8000)   │
│ (App/Browser)│                    │  FastAPI + Uvicorn ASGI Server    │
│             │ <────────────────── │                                   │
└─────────────┘     HTTP/JSON      │  Endpoints: /route, /matrix,      │
                                    │  /match, /trip, /nearest, /tile,  │
                                    │  /vrp, /vrp/allocate, /health,    │
                                    │  /matrix-graph                    │
                                    └───────────┬───────────────────────┘
                                                │ HTTP (httpx AsyncClient)
                                                │ Port 5000
                                                ▼
                                    ┌───────────────────────────────────┐
                                    │    OSRM Backend (Port 5000)       │
                                    │  C++ Engine (MLD Algorithm)       │
                                    │  Profiles: car, bicycle, foot     │
                                    │  Data: costa-rica-latest.osrm     │
                                    └───────────────────────────────────┘
```

The system operates as a gateway between clients and the OSRM C++ backend. Clients interact exclusively with the Python FastAPI gateway, which translates high-level JSON requests into OSRM HTTP query parameters, and enriches responses with additional computation (graph building, VRP solving).

### 3.2 Composition View

**ID:** CMP-001  
**Title:** Module Decomposition  
**Viewpoint:** Composition  
**Representation:**

```
┌───────────────────────────────────────────────────────────────┐
│                  OSRM API Gateway                              │
├───────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  API Layer (app/main.py)                                 │  │
│  │  FastAPI application, 10 endpoints, error handling,      │  │
│  │  rate limiting (slowapi), request validation,            │  │
│  │  Prometheus /metrics, health probe, lifespan shutdown,   │  │
│  │  OpenTelemetry tracing middleware                        │  │
│  │  Uses: logging_config, metrics, cache, tracing           │  │
│  └──────────┬──────────────────────────────────────────────┘  │
│             │                                                 │
│    ┌────────┴─────────┬──────────────────┐                   │
│    ▼                  ▼                  ▼                    │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│  │ OSRM     │  │ GraphBuilder │  │ VrpService            │    │
│  │ Client   │  │ (graph_      │  │ (vrp_service.py)      │    │
│  │ (osrm_   │  │  builder.py) │  │ Location-Allocation + │    │
│  │ client   │  │ NetworkX Di- │  │ TSP Optimization      │    │
│  │ .py)     │  │ Graph from   │  │ Depends on:           │    │
│  │ Retry +  │  │ matrix data  │  │ OSRMClient            │    │
│  │ Cache    │  │              │  │                       │    │
│  └──────────┘  └──────────────┘  └──────────────────────┘    │
│                                                               │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────────┐  │
│  │ Tracing Layer  │  │ Logging Config │  │ Metrics Reporter  │  │
│  │ (tracing.py)   │  │ (logging_      │  │ (metrics.py)      │  │
│  │ OpenTelemetry  │  │  config.py)    │  │ Prometheus-       │  │
│  │ FastAPI + httpx│  │ Structured     │  │ fastapi-          │  │
│  │ instrumentation│  │ JSON logging   │  │ instrumentator    │  │
│  └────────────────┘  └────────────────┘  └───────────────────┘  │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Cache Layer                                              │  │
│  │  ┌────────────────┐    ┌────────────────────────────┐    │  │
│  │  │ L1: In-Memory  │    │ L2: Redis Cache            │    │  │
│  │  │ (services/     │    │ (services/redis_cache.py)  │    │  │
│  │  │  cache.py)     │    │ Shared across instances,   │    │  │
│  │  │ TTLCache       │───>│ survives restarts          │    │  │
│  │  │ 15-min TTL     │    │ 15-min TTL, 1024 maxsize   │    │  │
│  │  └────────────────┘    └────────────────────────────┘    │  │
│  │  Cache-Aside pattern: read L1 → miss → read L2           │  │
│  │  → miss → fetch OSRM → populate L2 → populate L1        │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Models Layer (app/models/schemas.py)                    │  │
│  │  15 Pydantic v2 models: Coordinate, Stop, RouteReq,     │  │
│  │  MatchReq, MatrixReq, TripReq, NearestReq, VrpReq,      │  │
│  │  VrpResponse, VehicleRoute, etc.                        │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Config Layer (app/config.py)                            │  │
│  │  Pydantic Settings: OSRM_BASE_URL, rate limits,         │  │
│  │  APP_NAME, loaded from .env                              │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

### 3.3 Logical View

**ID:** LOG-001  
**Title:** Core Type Hierarchy  
**Viewpoint:** Logical  
**Representation:**

```
┌─────────────────────────────┐
│  BaseModel (Pydantic)       │
└──────────┬──────────────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
┌──────────┐ ┌─────────────┐
│Coordinate│ │CommonRouting│
│          │ │Options      │
│longitude │ │bearings     │
│latitude  │ │radiuses     │
└────┬─────┘ │hints        │
     │       │approaches   │
     ▼       │exclude      │
┌──────────┐ │snapping     │
│Stop      │ │skip_waypoints│
│id: str/int│└──────┬──────┘
└──────────┘        │
           ┌────────┼────────┬───────────┐
           ▼        ▼        ▼           ▼
     ┌────────┐┌────────┐┌────────┐ ┌─────────┐
     │RouteReq││MatchReq││Matrix  │ │TripReq  │
     │origin  ││bread-  ││Req     │ │coords   │
     │dest    ││crumbs  ││coords  │ │roundtrip│
     │waypoints││profile ││annot.  │ │source   │
     │steps   ││gaps    ││fallback│ │dest     │
     └────────┘│tidy    │└────────┘ └─────────┘
               └────────┘
                              ┌──────────────┐
                         ┌───>│VrpResponse   │
                         │    │code: str     │
                         │    │routes: List  │
                         │    │total_distance│
                         │    └──────────────┘
┌────────────┐            │
│VrpRequest  │────────────┤
│depots      │            │    ┌──────────────────┐
│stops       │────────────┼───>│VrpAllocationResp  │
│vehicle_ct  │            │    │code: str          │
│capacity    │            │    │allocations: Dict  │
│max_radius  │            │    │unreachable_stops  │
│clustering  │            │    └──────────────────┘
│hysteresis  │            │
│roundtrip   │            │    ┌──────────────┐
└────────────┘            └───>│VehicleRoute  │
                               │vehicle_id    │
                               │depot_index   │
                               │stops_indices │
                               │route_geometry│
                               │distance_meters│
                               │duration_secs │
                               └──────────────┘
```

**ID:** LOG-002  
**Title:** Service Layer Classes  
**Viewpoint:** Logical  
**Representation:**

```
┌──────────────────────────────┐
│ OSRMClient                              │
├─────────────────────────────────────────┤
│ - base_url: str                         │
│ - _client: AsyncClient                  │
├─────────────────────────────────────────┤
│ + close()                               │
│ + get_route(coords, req)                │
│ + get_matrix(req)                       │
│ + match_trace(req)                      │
│ + get_trip(req)                         │
│ + get_nearest(req)                      │
│ + get_tile(profile,z,x,y)               │
│ - _get(endpoint, params)                │
│ + _serialize_common_options             │
│ - _retryable(exc) → bool                │
│ - _fetch_with_retry(end,par)            │
└─────────────────────────────────────────┘

┌────────────────────────────────┐
│ GraphBuilder                   │
├────────────────────────────────┤
│ + build_from_matrix(data, req) │
│   → node_link_data (JSON)      │
└────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│ VrpService                                       │
├──────────────────────────────────────────────────┤
│ - osrm_client: OSRMClient                        │
├──────────────────────────────────────────────────┤
│ + solve_vrp(req) → VrpResponse                   │
│ + allocate_products(req) → VrpAllocationResponse │
│ - _get_allocation_data(req)                      │
│ - _solve_tsp_chunk(...) → VehicleRoute           │
│ - _get_depot_to_stop_matrix(depots, stops)       │
│ - _allocate_stops(durations, distances, ...)     │
└──────────────────────────────────────────────────┘

┌──────────────────────────┐
│ Settings                 │
├──────────────────────────┤
│ OSRM_BASE_URL: str       │
│ APP_NAME: str            │
│ DEBUG: bool              │
│ REDIS_URL: str           │
│ OTLP_ENDPOINT: str       │
│ RATE_LIMIT_*: str        │
└──────────────────────────┘
```

### 3.4 Information View

**ID:** INF-001  
**Title:** Request/Response Data Dictionary  
**Viewpoint:** Information  
**Representation:**

| Model | Field | Type | Constraints | Description |
|-------|-------|------|-------------|-------------|
| Coordinate | longitude | float | [-180, 180] | WGS84 longitude |
| Coordinate | latitude | float | [-90, 90] | WGS84 latitude |
| Stop | id | str\|int\|null | optional | Unique stop identifier |
| RouteRequest | origin | Coordinate | required | Start point |
| RouteRequest | destination | Coordinate | required | End point |
| RouteRequest | waypoints | List[Coord] | max 200 | Intermediate stops |
| RouteRequest | profile | enum | driving/cycling/walking | Routing profile |
| RouteRequest | steps | bool | default true | Turn-by-turn instructions |
| RouteRequest | alternatives | bool\|int | default false | Alternate routes |
| MatchRequest | breadcrumbs | List[GPSBreadcrumb] | [2, 5000] | GPS trace points |
| MatrixRequest | coordinates | List[Coordinate] | [2, 5000] | Points for matrix |
| MatrixRequest | annotations | enum | duration/distance/both | Cost metrics |
| TripRequest | coordinates | List[Coordinate] | [2, 200] | Points to optimize |
| VrpRequest | depots | List[Stop] | [1, 500] | Warehouse locations |
| VrpRequest | stops | List[Stop] | [1, 10000] | Delivery points |
| VrpRequest | capacity | int | [1, 10000], default 35 | Per-vehicle capacity |
| VrpRequest | clustering_mode | enum | distance/travel_time/radial | Allocation strategy |
| VehicleRoute | route_geometry | Dict | GeoJSON | Optimized route path |
| VehicleRoute | distance_meters | float | >= 0 | Total route distance |
| VehicleRoute | duration_seconds | float | >= 0 | Total route duration |

### 3.5 Interface View

**ID:** INT-001  
**Title:** REST API Surface  
**Viewpoint:** Interface  
**Representation:**

| Method | Path | Request Body | Response | Rate Limit |
|--------|------|-------------|----------|------------|
| GET | /health | — | `{status, service}` | — |
| POST | /route | RouteRequest | OSRM Route JSON | 600/min |
| POST | /matrix | MatrixRequest | OSRM Table JSON | 300/min |
| POST | /matrix-graph | MatrixRequest | `{nodes, edges}` | 300/min |
| POST | /match | MatchRequest | OSRM Match JSON | 600/min |
| POST | /trip | TripRequest | OSRM Trip JSON | 300/min |
| POST | /nearest | NearestRequest | OSRM Nearest JSON | 600/min |
| GET | /tile/{p}/{z}/{x}/{y}.mvt | — | application/x-protobuf | 600/min |
| POST | /vrp | VrpRequest | VrpResponse | 100/min |
| POST | /vrp/allocate | VrpRequest | VrpAllocationResponse | 100/min |

**Common error envelope:**
```
HTTP 400/422/500
{"detail": {"code": "InvalidValue", "message": "..."}}
```

**OSRM upstream interface** (internal, consumed by OSRMClient):
```
GET /{service}/v1/{profile}/{coordinates}?{params}
Services: route, table, match, trip, nearest, tile
Profiles: driving, cycling, walking
```

### 3.6 Interaction View

**ID:** INT-ACT-001  
**Title:** VRP Solve Flow  
**Viewpoint:** Interaction  
**Representation:**

```
Client          API Gateway          OSRMClient          OSRM Backend
  │                  │                    │                   │
  │ POST /vrp        │                    │                   │
  │─────────────────>│                    │                   │
  │                  │                    │                   │
  │                  │ solve_vrp(req)     │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │                  │ _get_allocation    │                   │
  │                  │   _data(req)       │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │                  │ _get_depot_to_stop │                   │
  │                  │   _matrix(d,s)     │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /table/v1/... │
  │                  │                    │ [batched 500]    │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │  JSON (durations  │
  │                  │                    │  + distances)     │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return matrix      │                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │                  │ _allocate_stops(   │                   │
  │                  │   durations,       │                   │
  │                  │   distances, ...)  │                   │
  │                  │  ┌─────────────────┐                   │
  │                  │  │ For each stop:  │                   │
  │                  │  │ 1. Find best    │                   │
  │                  │  │    depot by     │                   │
  │                  │  │    target met.  │                   │
  │                  │  │ 2. Apply        │                   │
  │                  │  │    hysteresis   │                   │
  │                  │  │ 3. Check sanity │                   │
  │                  │  │ 4. Apply radius │                   │
  │                  │  │    constraint   │                   │
  │                  │  └─────────────────┘                   │
  │                  │                    │                   │
  │                  │ for each cluster:  │                   │
  │                  │ _solve_tsp_chunk   │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /trip/v1/...  │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON (optimized)  │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return VehicleRoute│                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │                  │ VrpResponse        │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │ 200 JSON         │                    │                   │
  │<─────────────────│                    │                   │
```

**ID:** INT-ACT-002  
**Title:** Simple Route Request Flow  
**Viewpoint:** Interaction  
**Representation:**

```
Client          API Gateway          OSRMClient          OSRM Backend
  │                  │                    │                   │
  │ POST /route      │                    │                   │
  │─────────────────>│                    │                   │
  │                  │ origin+dest+       │                   │
  │                  │ waypoints→coords   │                   │
  │                  │                    │                   │
  │                  │ get_route(coords,  │                   │
  │                  │   request)         │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /route/v1/... │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON response     │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return raw JSON    │                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │ 200 JSON         │                    │                   │
  │<─────────────────│                    │                   │
```

### 3.7 Algorithm View

**ID:** ALG-001  
**Title:** VRP Location-Allocation with Hysteresis  
**Viewpoint:** Algorithm  
**Representation:**

**Purpose:** Assign each delivery stop to the optimal depot (warehouse) considering road network costs and stability.

**Inputs:**
- `durations`: `[num_depots × num_stops]` matrix of travel times (seconds)
- `distances`: `[num_depots × num_stops]` matrix of road distances (meters)
- `depots`: list of depot Coordinates
- `stops`: list of stop Coordinates
- `max_radius_m`: optional distance cap
- `mode`: `"travel_time"` or `"distance"` or `"radial"`
- `hysteresis_m`: buffer preventing flapping (default 2000m)

**Pseudocode:**

```
1. SELECT target_matrix = durations if mode=="travel_time" else distances
2. COMPUTE euclidean distances from every stop to every depot
   (using DEG_TO_M ≈ 110600m/degree, cos(lat) ≈ 0.98 for longitude)
3. FOR each stop s:
   a. anchor_depot = argmin(euclidean distance to s)
   b. IF mode == "radial":
        ASSIGN s to anchor_depot; CONTINUE
   c. best_depot = argmin(target_matrix[:, s])
   d. IF euclidean(best_depot) - euclidean(anchor) > 50km:
        USE anchor_depot (visual sanity override)
   e. IF best_val or anchor_val ≈ infinity:
        USE the reachable one
   f. ELSE apply hysteresis:
        IF target[best] < target[anchor] - hysteresis:
          ASSIGN to best_depot
        ELSE:
          ASSIGN to anchor_depot
   g. IF max_radius and distance > max_radius:
        MARK as unreachable
      ELSE:
        ADD to assignment
4. RETURN {allocations, unreachable_stops}
```

**Hysteresis conversion (time mode):** `effective_hysteresis = hysteresis_m / 11.1` seconds (≈ 2km at 40km/h).

**Chunked TSP (Phase 2):**

```
For each (depot, cluster) pair:
  1. SUB-PARTITION cluster into chunks of min(80, capacity)
  2. FOR each chunk:
     a. CONSTRUCT TripRequest(depot + chunk, source="first", 
                              destination="any", roundtrip=request.roundtrip)
     b. CALL OSRM /trip/v1/{profile}/{coords}
     c. REORDER stops per waypoint.trips_index and waypoint_index
     d. MAP sorted waypoints back to original stop indices
     e. RETURN VehicleRoute with geometry, distance, duration
  3. AGGREGATE routes into VrpResponse
```

### 3.8 Deployment View

**ID:** DEP-001  
**Title:** Docker Compose Topology  
**Viewpoint:** Deployment  
**Representation:**

```
┌─────────────────────────────────────────────────────────────┐
│                   Docker Host                                │
│                                                              │
│  ┌─ osrm-data-builder ─────────────────────────────────┐     │
│  │  Image: osrm-data-builder:latest                     │     │
│  │  Profile: build (manual execution)                   │     │
│  │  CMD: osrm-extract → osrm-partition → osrm-customize │     │
│  │  Data: costa-rica-latest.osm.pbf → .osrm files       │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─ osrm ───────────────────────────────────────────────┐     │
│  │  Image: osrm-backend:latest (multi-stage Dockerfile)  │     │
│  │  Container: osrm-backend                              │     │
│  │  Port: 5000 → 5000                                    │     │
│  │  CMD: osrm-routed --algorithm mld --max-trip-size 200 │     │
│  │  Profiles: car (default), bicycle, foot               │     │
│  │  Volume: /data/car/ ← processed .osrm files           │     │
│  │  Platform: linux/amd64                                │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─ redis ──────────────────────────────────────────────┐     │
│  │  Image: redis:7-alpine                                │     │
│  │  Container: osrm-cache                                │     │
│  │  Port: 6379 → 6379                                    │     │
│  │  CMD: redis-server --save "" --appendonly no          │     │
│  │  (pure cache, no persistence needed)                  │     │
│  │  Restart: always                                      │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─ api ────────────────────────────────────────────────┐     │
│  │  Image: osrm-api-gateway (Dockerfile)                  │     │
│  │  Container: osrm-api-gateway                          │     │
│  │  Port: 8080 → 8000 (FastAPI internal)                 │     │
│  │  ENV: OSRM_BASE_URL=http://osrm-backend:5000          │     │
│  │  ENV: REDIS_URL=redis://osrm-cache:6379/0             │     │
│  │  CMD: uvicorn app.main:app --host 0.0.0.0 --port 8000│     │
│  │  Depends: osrm, redis (health check)                  │     │
│  │  Restart: always                                      │     │
│  └──────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

**Data flow at deploy:** The `osrm-data-builder` runs once (build profile) to process raw OSM PBF data into routing graph files. The `osrm` container loads these files and serves the routing API. The `api` container connects to `osrm` via internal Docker networking. The `redis` container provides a shared cache layer — the `api` container writes route/matrix responses on TTL and reads from Redis before falling through to OSRM. Tracing spans are exported via OTLP to an external collector.

### 3.9 Concurrency View

**ID:** CONC-001  
**Title:** Async I/O and Rate Limiting  
**Viewpoint:** Concurrency  
**Representation:**

The system uses a single-threaded async model:

1. **ASGI Server:** Uvicorn runs the FastAPI application with async workers. All endpoint handlers are `async def`.
2. **HTTP Client Pool:** `OSRMClient` initializes a single `httpx.AsyncClient` with connection pooling, reused across all requests. Default timeout: 30s.
3. **Rate Limiting:** `slowapi` middleware with per-endpoint limits enforced via `@limiter.limit(...)` decorator. Key function: `get_remote_address`. Configuration:
   - `/route`: 600 req/min
   - `/matrix`, `/matrix-graph`: 300 req/min
   - `/match`: 600 req/min
   - `/trip`: 300 req/min
   - `/vrp`, `/vrp/allocate`: 100 req/min
   - `/nearest`, `/tile`: 600 req/min
4. **Redis client:** `redis.asyncio.Redis` connection pool is created at startup and reused across requests. All cache operations are non-blocking `await` calls.
5. **Tracing:** OpenTelemetry spans are created per-request and propagated to `httpx.AsyncClient` via instrumentor hooks. Span export is asynchronous (batch processor).
6. **No threading/parallelism:** The VRP service processes allocations and TSP chunks sequentially within a single request. Matrix batching (500 stops/batch) uses sequential `await` calls.

### 3.10 Patterns View

**ID:** PAT-001  
**Title:** Architectural Patterns Used  
**Viewpoint:** Patterns  
**Representation:**

| Pattern | Application | Location |
|---------|-------------|----------|
| **Gateway** | Single entry point translating between client and backend | `app/main.py` (all endpoints) |
| **Proxy** | Pass-through to OSRM with enriched request/response | `OSRMClient` methods |
| **Service Layer** | Business logic encapsulated in service classes | `app/services/*` |
| **Dependency Injection** | `VrpService` receives `OSRMClient` via constructor | `VrpService.__init__(osrm_client)` |
| **Builder** | `GraphBuilder` constructs a complex object (NetworkX graph) from matrix data | `GraphBuilder.build_from_matrix()` |
| **Settings** | Environment-based configuration via Pydantic BaseSettings | `app/config.py:Settings` |
| **Strategy** | `clustering_mode` selects allocation algorithm (distance/time/radial) | `_allocate_stops()` mode parameter |
| **Batch Processing** | Large matrices split into batches of 500 stops | `_get_depot_to_stop_matrix()` |
| **Cache-Aside** | Application reads L1 (in-memory), falls through to L2 (Redis), then to source (OSRM) | `OSRMClient._get()` + `response_cache` + `RedisCache` |
| **OpenTelemetry Tracing** | Distributed tracing middleware auto-instruments FastAPI and httpx, exporting spans via OTLP | `app/tracing.py` |

---

## 4. Decisions

**ID:** DEC-001  
**Title:** Gateway Architecture over Direct OSRM Exposure  
**Context:** Clients could call OSRM directly, but this would expose raw HTTP query parameters, lack rate limiting, and force every client to implement OSRM's semicolon-delimited parameter encoding.  
**Options:** (a) Direct OSRM proxy, (b) Full gateway with validation and enrichment, (c) GraphQL layer.  
**Outcome:** Chosen option (b) — Full FastAPI gateway with Pydantic validation, rate limiting, and rich JSON request/response bodies. Provides a stable, documented API surface.  
**More Information:** Gateway pattern simplifies client integration and centralizes cross-cutting concerns.

**ID:** DEC-002  
**Title:** Async Throughout  
**Context:** The system makes many outbound HTTP calls that block on I/O. Using synchronous code would limit throughput under concurrent requests.  
**Options:** (a) Sync with thread pool, (b) Full async with FastAPI + httpx, (c) ASGI with sync endpoints.  
**Outcome:** Chosen option (b) — All endpoints and service methods are async, using a single `httpx.AsyncClient` with connection pooling. Maximizes throughput under concurrent load.

**ID:** DEC-003  
**Title:** VRP Architecture: Two-Phase Allocation + TSP  
**Context:** OSRM does not natively support multi-vehicle routing. A custom VRP solver was needed that delegates routing to OSRM's `/trip` service.  
**Options:** (a) Pure OSRM trip calls per vehicle (no allocation), (b) Location-Allocation + TSP, (c) External VRP solver (OR-Tools, jsprit).  
**Outcome:** Chosen option (b) — Location-Allocation with hysteresis clustering, then per-cluster TSP via OSRM `/trip`. Balances algorithmic quality with OSRM reuse.  
**More Information:** see `docs/planning/vrp_proposal.md`.

**ID:** DEC-004  
**Title:** Hysteresis-Based Assignment Stability  
**Context:** Stops near depot boundaries could flip assignments between requests due to small measurement variations, causing inconsistent routes.  
**Options:** (a) Always pick the closest depot, (b) Hysteresis buffer, (c) Random tie-breaking.  
**Outcome:** Chosen option (b) — A 2000m buffer (configurable) prevents flapping: a stop stays with its current depot unless a different depot is `hysteresis_m` better. Also includes a 50km Euclidean sanity check.

**ID:** DEC-005  
**Title:** Pydantic v2 Models over Raw Dicts  
**Context:** All API requests need validation, serialization, and documentation.  
**Options:** (a) Raw dict parsing, (b) Pydantic v1, (c) Pydantic v2, (d) Marshmallow.  
**Outcome:** Chosen option (c) — Pydantic v2 with `BaseModel` and `Field` constraints provides automatic validation, JSON Schema generation for OpenAPI docs, and fast serialization.

**ID:** DEC-006  
**Title:** Multi-Stage Docker Builds for OSRM Data Processing  
**Context:** OSRM data extraction is slow (~30min) and requires different tools than the runtime server.  
**Options:** (a) Single-stage build with all tools, (b) Multi-stage with separate builder and runtime, (c) Pre-processed data volume.  
**Outcome:** Chosen option (b) — Three Dockerfiles: `Dockerfile.builder` (extract/partition/customize), `Dockerfile.osrm` (runtime), and `Dockerfile` (API gateway, repo root). Only the runtime images are used in production.

**ID:** DEC-007  
**Title:** slowapi for Rate Limiting  
**Context:** Endpoints need protection from excessive usage.  
**Options:** (a) Custom middleware, (b) slowapi, (c) Nginx-level rate limiting.  
**Outcome:** Chosen option (b) — `slowapi` with in-memory rate tracking. Simple configuration per endpoint via decorators. Nginx could be added upstream for distributed deployments.

**ID:** DEC-008  
**Title:** Retry with Exponential Backoff for Transient OSRM Failures  
**Context:** OSRM can return sporadic 5xx or timeouts under load. A single failure would cascade into a 500 error to the client.  
**Options:** (a) Let failures propagate, (b) Retry with fixed delay, (c) Exponential backoff with jitter.  
**Outcome:** Chosen option (c) — `tenacity` with exponential backoff (1s → 10s max, 3 attempts). Only retries on 5xx, timeouts, and transport errors. 4xx errors (client errors) are never retried.

**ID:** DEC-009  
**Title:** Response Caching for Repeated OSRM Queries  
**Context:** The same route or matrix request may be submitted multiple times within minutes (e.g., dashboard auto-refresh). Unnecessary OSRM calls waste backend resources.  
**Options:** (a) No cache, (b) In-memory TTL cache, (c) Redis cache.  
**Outcome:** Chosen option (b) — `cachetools.TTLCache` with 15-minute TTL and 1024-entry limit. Keyed by endpoint + sorted parameter hash. Cache-first strategy: `_get` returns cached data immediately, falling through to OSRM on miss. Avoids Redis operational overhead for the current scale.

**ID:** DEC-010  
**Title:** Prometheus Metrics for Observability  
**Context:** The system had no request latency, error rate, or throughput visibility. Operators could not monitor service health or detect degradation.  
**Options:** (a) No metrics, (b) Prometheus client with custom instrumentation, (c) OpenTelemetry exporter.  
**Outcome:** Chosen option (b) — `prometheus-fastapi-instrumentator` auto-instruments all endpoints. Exposes `/metrics` in Prometheus text format. Compatible with Grafana dashboards and existing monitoring infrastructure.  
**Note:** Combined with DEC-012 (tracing), metrics provide RED (Rate/Errors/Duration) signals while traces provide individual request context.

**ID:** DEC-012  
**Title:** OpenTelemetry Distributed Tracing  
**Context:** The system spans two services (API Gateway + OSRM Backend) and makes multiple outbound HTTP calls per request. Latency breakdowns and end-to-end visibility are needed to diagnose performance issues. The Prometheus metrics (DEC-010) show aggregate rates but cannot pinpoint which specific requests are slow or where time is spent.  
**Options:** (a) No tracing, (b) Custom correlation IDs in request headers, (c) OpenTelemetry with W3C TraceContext propagation.  
**Outcome:** Chosen option (c) — OpenTelemetry SDK with `opentelemetry-instrumentation-fastapi` and `opentelemetry-instrumentation-httpx`. Auto-instruments all endpoints and outbound HTTP calls. Spans exported via OTLP to a configurable collector endpoint. W3C `traceparent` headers propagate to the OSRM backend for end-to-end correlation.  
**Configuration:** `OTLP_ENDPOINT` env var (default `http://localhost:4318/v1/traces`). The tracing layer is optional — if the endpoint is unreachable, spans are dropped without affecting request processing.  
**Dependencies:** `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`, `opentelemetry-exporter-otlp-proto-http`.

**ID:** DEC-011  
**Title:** FastAPI Lifespan for Graceful Connection Pool Shutdown  
**Context:** The `httpx.AsyncClient` connection pool was never explicitly closed, leading to `unclosed transport` warnings and potential resource leaks on shutdown.  
**Outcome:** Wired `OSRMClient.close()` into FastAPI's `lifespan` context manager. The pool is now gracefully torn down when the ASGI server stops.

**ID:** DEC-013  
**Title:** Redis-Backed Distributed Cache  
**Context:** The in-memory TTLCache (DEC-009) is lost on process restart, not shared across API replicas, and limited to 1024 entries. Running as part of controlled infrastructure means horizontal scaling is a realistic requirement — each replica would otherwise have a cold cache.  
**Options:** (a) Keep in-memory only, (b) Redis as L2 behind in-memory L1, (c) Redis-only (no local cache).  
**Outcome:** Chosen option (b) — Two-level cache: L1 is the existing `cachetools.TTLCache` (sub-millisecond local reads), L2 is Redis (shared across instances, survives restarts). Cache-Aside pattern: `_get()` checks L1, then L2, then OSRM. Each level populates the one above it on miss. Redis is configured without persistence (`--save "" --appendonly no`) since cache data is always derivable from OSRM.  
**Configuration:** `REDIS_URL` env var (default `redis://localhost:6379/0`). Graceful degradation: if Redis is unreachable, the cache falls back to L1 only.  
**Dependencies:** `redis[asyncio]>=5.2.0`.

---

## 5. Appendixes

### 5.1 VRP Mathematical Formulation

**Location-Allocation Phase:**

Given depots `D = {d₁, ..., dₘ}` and stops `S = {s₁, ..., sₙ}`, with cost matrix `C ∈ ℝ^{m×n}` where `C[i][j]` is the travel time (or distance) from depot `dᵢ` to stop `sⱼ`:

Assign each stop `sⱼ` to exactly one depot `dᵢ` such that:

```
assignment(sⱼ) = dᵢ  where  i = argmin_k C[k][j]
```

Subject to:
- **Hysteresis:** `C[best][j] < C[anchor][j] - h` for reassignment
- **Sanity:** `euclidean(best) - euclidean(anchor) < 50km`
- **Max radius:** `road_distance(i, j) ≤ max_radius_km`
- **Radial mode:** Uses Euclidean distance instead of road cost

**TSP Phase (per cluster):**

For each depot `d` with assigned stops `S' = {s'₁, ..., s'ₖ}`:

Find permutation `π` minimizing total round-trip cost:

```
minimize     distance(d, s'_{π₁}) + Σ_{t=1}^{k-1} distance(s'_{πₜ}, s'_{π_{t+1}}) + distance(s'_{πₖ}, d)
subject to   1 ≤ πₜ ≤ k,  ∀t
             πₜ ≠ πₛ  for t ≠ s
```

Delegated to OSRM `/trip` service which uses specialized heuristics on the contraction hierarchy.

### 5.2 Rate Limiting Configuration

| Endpoint | Limit | Window |
|----------|-------|--------|
| /route | 600 | 1 minute |
| /matrix | 300 | 1 minute |
| /matrix-graph | 300 | 1 minute |
| /match | 600 | 1 minute |
| /trip | 300 | 1 minute |
| /nearest | 600 | 1 minute |
| /tile | 600 | 1 minute |
| /vrp | 100 | 1 minute |
| /vrp/allocate | 100 | 1 minute |

Configured via `app/config.py:Settings` with environment variable overrides.

### 5.3 Redis Cache Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `REDIS_TTL` | `900` (15 min) | Cache TTL in seconds |
| `REDIS_MAXSIZE` | `1024` | Max cached entries (approximate, Redis LRU eviction) |

The two-level cache hierarchy:
1. **L1 (in-memory):** `cachetools.TTLCache` at `app/services/cache.py` — sub-millisecond reads, local to process, lost on restart.
2. **L2 (Redis):** `app/services/redis_cache.py` — shared across instances, survives restarts, configured as pure cache (no persistence).

**Cache-Aside flow** (`OSRMClient._get()`):
```
READ:  L1 hit? → return
       L1 miss → L2 hit? → populate L1, return
       L2 miss → fetch OSRM → populate L2 → populate L1 → return
```

**Graceful degradation:** If Redis is unreachable at startup or during operation, the cache falls back to L1-only. No request fails due to cache layer errors. Logged at WARNING level.
