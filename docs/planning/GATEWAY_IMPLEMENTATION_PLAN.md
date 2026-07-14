# Gateway Implementation Plan: Full OSRM API Coverage

**Document:** `docs/GATEWAY_IMPLEMENTATION_PLAN.md`
**Project:** `osrm-api-gateway` v0.2.2
**Date:** 2026-04-22
**Status:** Draft

---

## Executive Summary

The OSRM API Gateway currently wraps four OSRM services (Route, Table, Match, Trip) and exposes them through eight FastAPI endpoints. The implementation is functional but substantially under-exposes the upstream OSRM API surface. Two entire OSRM services (Nearest and Tile) are absent, and dozens of parameters across the four existing services are either hardcoded to a single value or silently ignored.

The most significant structural limitation is that `profile` is hardcoded to `"driving"` in every URL constructed inside `osrm_client.py`. This means callers who need the `cycling` or `walking` profiles must run a separate deployment, even though the underlying OSRM binary already supports all three profiles.

This plan resolves every identified gap across four sequenced phases ordered by user-facing impact, implementation risk, and dependency relationships. All changes are contained to three core files (`app/models/schemas.py`, `app/services/osrm_client.py`, `app/main.py`) with a single supporting change to `app/config.py` for the new rate limit constant introduced in Phase 2.

The plan is intentionally additive: every new field carries a default value that reproduces current behaviour, so no existing client breaks.

---

## Gap Analysis

The table below maps each OSRM parameter to its current gateway status. "Hardcoded" means the gateway always sends a fixed value regardless of the caller's intent. "Missing" means the parameter is never sent at all. "Exposed" means it is already a field in the request schema and forwarded to OSRM.

| OSRM Service | Parameter | Current Status | Target Status | Phase |
|---|---|---|---|---|
| All services | `profile` (car/bike/foot) | Hardcoded `driving` in URL | Optional field, default `"driving"` | 1 |
| Route | `overview` | Hardcoded `full` | Exposed, default `"full"` | 1 |
| Route | `geometries` | Hardcoded `geojson` | Exposed, default `"geojson"` | 1 |
| Route | `steps` | Hardcoded `true` | Exposed, default `true` | 1 |
| Route | `annotations` | Hardcoded `distance,duration` | Exposed, default `"distance,duration"` | 1 |
| Route | `continue_straight` | Missing | Exposed, default `None` (omitted) | 1 |
| Route | `alternatives` | Exposed | No change | — |
| Route | `waypoints` | Exposed (forced all indices) | No change needed | — |
| Route | `bearings` | Missing | Exposed optional | 3 |
| Route | `radiuses` | Missing | Exposed optional | 3 |
| Route | `hints` | Missing | Exposed optional | 3 |
| Route | `approaches` | Missing | Exposed optional | 3 |
| Route | `exclude` | Missing | Exposed optional | 3 |
| Route | `snapping` | Missing | Exposed optional | 3 |
| Route | `skip_waypoints` | Missing | Exposed optional | 3 |
| Table | `annotations` | Hardcoded `duration,distance` | Exposed, default `"duration,distance"` | 1 |
| Table | `fallback_speed` | Missing | Exposed optional | 1 |
| Table | `fallback_coordinate` | Missing | Exposed optional | 1 |
| Table | `scale_factor` | Missing | Exposed optional | 1 |
| Table | `sources` | Exposed | No change | — |
| Table | `destinations` | Exposed | No change | — |
| Table | `bearings` | Missing | Exposed optional | 3 |
| Table | `radiuses` | Missing | Exposed optional | 3 |
| Table | `approaches` | Missing | Exposed optional | 3 |
| Table | `exclude` | Missing | Exposed optional | 3 |
| Table | `skip_waypoints` | Missing | Exposed optional | 3 |
| Table | `snapping` | Missing | Exposed optional | 3 |
| Match | `overview` | Hardcoded `full` | Exposed, default `"full"` | 1 |
| Match | `geometries` | Hardcoded `geojson` | Exposed, default `"geojson"` | 1 |
| Match | `steps` | Missing | Exposed, default `false` | 1 |
| Match | `annotations` | Missing | Exposed, default `None` (omitted) | 1 |
| Match | `gaps` | Missing | Exposed optional | 1 |
| Match | `tidy` | Missing | Exposed optional | 1 |
| Match | `waypoints` | Missing | Exposed optional (index list) | 1 |
| Match | `timestamps` | Exposed (from breadcrumbs) | No change | — |
| Match | `radiuses` | Exposed (from breadcrumbs) | No change | — |
| Match | `bearings` | Missing | Exposed optional | 3 |
| Match | `hints` | Missing | Exposed optional | 3 |
| Match | `approaches` | Missing | Exposed optional | 3 |
| Match | `exclude` | Missing | Exposed optional | 3 |
| Trip | `overview` | Hardcoded `full` | Exposed, default `"full"` | 1 |
| Trip | `geometries` | Hardcoded `geojson` | Exposed, default `"geojson"` | 1 |
| Trip | `steps` | Hardcoded `true` | Exposed, default `true` | 1 |
| Trip | `annotations` | Hardcoded `distance,duration` | Exposed, default `"distance,duration"` | 1 |
| Trip | `roundtrip` | Exposed | No change | — |
| Trip | `source` | Exposed | No change | — |
| Trip | `destination` | Exposed | No change | — |
| Trip | `bearings` | Missing | Exposed optional | 3 |
| Trip | `radiuses` | Missing | Exposed optional | 3 |
| Trip | `hints` | Missing | Exposed optional | 3 |
| Trip | `approaches` | Missing | Exposed optional | 3 |
| Trip | `exclude` | Missing | Exposed optional | 3 |
| Nearest | entire service | Missing | New endpoint + schema + client method | 2 |
| Tile | entire service | Missing | New GET proxy endpoint | 4 |
| All | Error body passthrough | Generic string only | Forward OSRM `code`+`message` | 4 |

---

## Phase 1 — Profile and Core Parameter Exposure

**Priority:** High
**Files changed:** `app/models/schemas.py`, `app/services/osrm_client.py`, `app/main.py`
**Risk:** Low — all new fields have defaults that replicate the current hardcoded behaviour exactly. No existing caller is broken.

### Rationale

The single most impactful change across the entire plan is removing the `driving` hardcode from every URL in `osrm_client.py`. Every OSRM URL is currently constructed as `/route/v1/driving/{coords}`. Adding `profile` as an optional field with a default of `"driving"` makes the gateway multi-modal with zero breaking changes. The remaining items in this phase follow the same principle: convert hardcoded constants into schema fields with defaults identical to the current hardcoded value.

There is one exception in the Match service: `steps` is currently not forwarded at all (it appears in the OSRM spec but was never added). The safe default is `false` since omitting it from the gateway response is the current observed behaviour.

### 1.1 Add shared type aliases in `app/models/schemas.py`

Add immediately after the imports, before any class definitions:

```python
# Type alias used by Route, Match, and Trip annotation fields
AnnotationValue = Literal[
    "true", "false", "nodes", "distance",
    "duration", "datasources", "weight", "speed"
]

# Restricted annotation subset accepted by the Table service
MatrixAnnotation = Literal["duration", "distance", "duration,distance"]
```

### 1.2 Changes to `RouteRequest`

Add to `RouteRequest` in `app/models/schemas.py`:

```python
profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
steps: bool = Field(True, description="Return step-by-step turn instructions")
annotations: Optional[str] = Field("distance,duration", description="Comma-separated metadata to annotate each segment (distance, duration, nodes, datasources, weight, speed)")
continue_straight: Optional[Literal["default", "true", "false"]] = Field(None, description="Force route to continue straight at intermediate waypoints")
```

`annotations` is `Optional[str]` rather than a strict `Literal` union because OSRM accepts comma-separated combinations (e.g., `"distance,duration,speed"`).

### 1.3 Changes to `MatchRequest`

Add to `MatchRequest`:

```python
profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
steps: bool = Field(False, description="Return step-by-step instructions for the matched route")
annotations: Optional[str] = Field(None, description="Comma-separated metadata annotations per segment")
gaps: Optional[Literal["split", "ignore"]] = Field(None, description="How to handle large timestamp gaps in the trace")
tidy: Optional[bool] = Field(None, description="Remove repeated or out-of-order coordinates before matching")
match_waypoints: Optional[List[int]] = Field(None, description="Indices into breadcrumbs to treat as explicit waypoints")
```

Note: the field is named `match_waypoints` (not `waypoints`) to avoid shadowing the breadcrumb-derived radius/timestamp logic and to be unambiguous in the schema context.

### 1.4 Changes to `MatrixRequest`

Add to `MatrixRequest`:

```python
profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
annotations: MatrixAnnotation = Field("duration,distance", description="Which cost annotations to return")
fallback_speed: Optional[float] = Field(None, gt=0, description="Speed (km/h) used to estimate travel time for unreachable pairs")
fallback_coordinate: Optional[Literal["input", "snapped"]] = Field(None, description="Which coordinate to use when a pair falls back to speed estimate")
scale_factor: Optional[float] = Field(None, gt=0, description="Multiply all durations by this factor (useful for congestion modelling)")
```

### 1.5 Changes to `TripRequest`

Add to `TripRequest`:

```python
profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
overview: Literal["simplified", "full", "false"] = Field("full", description="Level of overview geometry returned")
geometries: Literal["polyline", "polyline6", "geojson"] = Field("geojson", description="Geometry encoding format")
steps: bool = Field(True, description="Return step-by-step turn instructions")
annotations: Optional[str] = Field("distance,duration", description="Comma-separated metadata annotations per segment")
```

### 1.6 Update `OSRMClient.get_route()`

Change the method signature to accept the full `RouteRequest` as the second argument (one call site in `main.py`):

```python
# Before
async def get_route(self, coordinates: List[Dict[str, float]], alternatives: Union[bool, int] = False)

# After
async def get_route(self, coordinates: List[Dict[str, float]], request: RouteRequest) -> Dict[str, Any]:
```

Update the method body:
- Replace `/route/v1/driving/` → `/route/v1/{request.profile}/`
- Replace hardcoded param values:
  - `"overview": "full"` → `"overview": request.overview`
  - `"geometries": "geojson"` → `"geometries": request.geometries`
  - `"steps": "true"` → `"steps": "true" if request.steps else "false"`
  - `"annotations": "distance,duration"` → add only if `request.annotations is not None`
  - `alternatives` param: derive from `request.alternatives` (same logic as before)
- Add conditional: if `request.continue_straight is not None`, include `"continue_straight": request.continue_straight`

Update the single call site in `app/main.py` at line 42:
```python
# Before
return await osrm_client.get_route(points, alternatives=payload.alternatives)

# After
return await osrm_client.get_route(points, request=payload)
```

### 1.7 Update `OSRMClient.get_matrix()`

- Replace `/table/v1/driving/` → `/table/v1/{request.profile}/`
- Replace `{"annotations": "duration,distance"}` → `{"annotations": request.annotations}`
- Add conditionally to params:
  - `fallback_speed` if `request.fallback_speed is not None`
  - `fallback_coordinate` if `request.fallback_coordinate is not None`
  - `scale_factor` if `request.scale_factor is not None`

**VRP compatibility note:** `VrpService` constructs `MatrixRequest` objects internally without the new fields. All new fields default to values matching current hardcoded behaviour — no changes to `vrp_service.py` needed.

### 1.8 Update `OSRMClient.match_trace()`

- Replace `/match/v1/driving/` → `/match/v1/{request.profile}/`
- Replace hardcoded params with fields from `request`:
  - `"overview": "full"` → `"overview": request.overview`
  - `"geometries": "geojson"` → `"geometries": request.geometries`
- Add new params conditionally:
  - `"steps": "true" if request.steps else "false"`
  - `"annotations": request.annotations` if not `None`
  - `"gaps": request.gaps` if not `None`
  - `"tidy": "true" if request.tidy else "false"` if `request.tidy is not None`
  - `"waypoints": ";".join(map(str, request.match_waypoints))` if `request.match_waypoints is not None`

### 1.9 Update `OSRMClient.get_trip()`

- Replace `/trip/v1/driving/` → `/trip/v1/{request.profile}/`
- Replace hardcoded params with fields from `request`:
  - `"overview": "full"` → `"overview": request.overview`
  - `"geometries": "geojson"` → `"geometries": request.geometries`
  - `"steps": "true"` → `"steps": "true" if request.steps else "false"`
  - `"annotations": "distance,duration"` → add only if `request.annotations is not None`

**VRP compatibility note:** `VrpService._solve_tsp_chunk()` constructs `TripRequest` at line 169. New field defaults preserve current behaviour — no changes to `vrp_service.py` needed.

---

## Phase 2 — Nearest Service

**Priority:** Medium
**Files changed:** `app/models/schemas.py`, `app/services/osrm_client.py`, `app/main.py`, `app/config.py`
**Risk:** Low — entirely additive, no existing code paths modified

### Rationale

The Nearest service is OSRM's simplest endpoint and the most commonly needed utility when building location-aware features: given a raw GPS coordinate, find the closest point(s) on the road network. It has no equivalent in the current gateway. Implementation is self-contained: one new schema pair, one new client method, one new endpoint.

### 2.1 Add `NearestRequest` to `app/models/schemas.py`

```python
class NearestRequest(BaseModel):
    """Request model for the OSRM Nearest service."""
    coordinate: Coordinate
    number: int = Field(1, ge=1, description="Number of nearest road segments to return")
    profile: Literal["driving", "cycling", "walking"] = Field("driving", description="Routing profile")
```

### 2.2 Add `NearestResponse` to `app/models/schemas.py`

```python
class NearestResponse(BaseModel):
    """Response model for the OSRM Nearest service (pass-through)."""
    code: str
    waypoints: List[Dict[str, Any]]
```

Using `Dict[str, Any]` for waypoints avoids modelling the full OSRM internal waypoint structure and matches the existing pattern throughout the codebase.

### 2.3 Add `get_nearest()` to `OSRMClient`

```python
async def get_nearest(self, request: NearestRequest) -> Dict[str, Any]:
    """Find the nearest road network point(s) to a given coordinate."""
    coord_str = f"{request.coordinate.longitude},{request.coordinate.latitude}"
    return await self._get(
        f"/nearest/v1/{request.profile}/{coord_str}",
        params={"number": request.number}
    )
```

### 2.4 Add rate limit constant to `app/config.py`

```python
RATE_LIMIT_NEAREST: str = "600/minute"
```

### 2.5 Add `POST /nearest` endpoint to `app/main.py`

Add `NearestRequest` and `NearestResponse` to the import block at the top of `main.py`. Add the endpoint grouped with the routing endpoints:

```python
@app.post("/nearest", tags=["Routing"], summary="Snap Coordinate to Road Network")
@limiter.limit(settings.RATE_LIMIT_NEAREST)
async def get_nearest(request: Request, payload: NearestRequest):
    """
    Find the nearest road network node(s) to a given coordinate.
    Useful for snapping raw GPS coordinates to the routable road graph.
    """
    try:
        return await osrm_client.get_nearest(payload)
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /nearest: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail="Routing service error")
    except Exception:
        logger.exception("Unexpected error on /nearest")
        raise HTTPException(status_code=500, detail="Internal server error")
```

---

## Phase 3 — General Options (Shared Optional Parameters)

**Priority:** Medium-Low
**Files changed:** `app/models/schemas.py`, `app/services/osrm_client.py`
**Risk:** Low-Medium — additive to schemas, but requires careful semicolon/comma serialization in the client

### Rationale

OSRM defines a set of optional "general options" applicable across most services: `bearings`, `radiuses`, `hints`, `approaches`, `exclude`, `snapping`, and `skip_waypoints`. These enable advanced snapping control, routing constraints, and response optimization. None are currently exposed. They are grouped together because they share identical serialization logic: most are per-coordinate semicolon-delimited lists, sent to OSRM only when non-null.

### 3.1 Add `CommonRoutingOptions` base class in `app/models/schemas.py`

Insert before the request schema class definitions:

```python
class CommonRoutingOptions(BaseModel):
    """
    Optional OSRM general options applicable to Route, Table, Match, and Trip services.
    All fields are optional and omitted from the upstream request when not set.
    Per-coordinate lists must have exactly one entry per coordinate (or be omitted entirely).
    """
    bearings: Optional[List[Optional[str]]] = Field(
        None,
        description="Per-coordinate bearing constraints as 'angle,deviation' strings (e.g. '90,30'). Use null for unconstrained coordinates."
    )
    radiuses: Optional[List[Optional[float]]] = Field(
        None,
        description="Per-coordinate snapping radius in meters. Use null for unlimited."
    )
    hints: Optional[List[Optional[str]]] = Field(
        None,
        description="Per-coordinate hint strings from a previous OSRM response to speed up snapping."
    )
    approaches: Optional[List[Optional[Literal["unrestricted", "curb"]]]] = Field(
        None,
        description="Per-coordinate approach side. Use null for default."
    )
    exclude: Optional[List[str]] = Field(
        None,
        description="Road classes to exclude globally (e.g. ['motorway', 'toll'])."
    )
    snapping: Optional[Literal["default", "any"]] = Field(
        None,
        description="Edge selection behaviour: 'default' (one-way roads respected) or 'any'."
    )
    skip_waypoints: Optional[bool] = Field(
        None,
        description="Suppress the waypoints array in the response to reduce payload size."
    )
```

**Serialization notes:**
- `bearings`: semicolon-delimited, `None` entries become empty segments (e.g., `"90,30;;180,45"`)
- `radiuses`: semicolon-delimited, `None` entries become `"unlimited"`
- `hints`: semicolon-delimited, `None` entries become empty strings
- `approaches`: semicolon-delimited, `None` entries become empty strings
- `exclude`: comma-delimited single value (global, not per-coordinate)
- `snapping`, `skip_waypoints`: simple scalar values

### 3.2 Update request schemas to inherit from `CommonRoutingOptions`

```python
class RouteRequest(CommonRoutingOptions):    # was BaseModel
class MatrixRequest(CommonRoutingOptions):   # was BaseModel
class MatchRequest(CommonRoutingOptions):    # was BaseModel
class TripRequest(CommonRoutingOptions):     # was BaseModel
class NearestRequest(CommonRoutingOptions):  # was BaseModel (skip_waypoints n/a for Nearest)
```

### 3.3 Add `_serialize_common_options()` static method to `OSRMClient`

```python
@staticmethod
def _serialize_common_options(request) -> Dict[str, Any]:
    """Serialize optional OSRM general options into query parameter entries."""
    params = {}
    if request.bearings is not None:
        params["bearings"] = ";".join(b if b is not None else "" for b in request.bearings)
    if request.radiuses is not None:
        params["radiuses"] = ";".join(str(r) if r is not None else "unlimited" for r in request.radiuses)
    if request.hints is not None:
        params["hints"] = ";".join(h if h is not None else "" for h in request.hints)
    if request.approaches is not None:
        params["approaches"] = ";".join(a if a is not None else "" for a in request.approaches)
    if request.exclude is not None:
        params["exclude"] = ",".join(request.exclude)
    if request.snapping is not None:
        params["snapping"] = request.snapping
    if request.skip_waypoints is not None:
        params["skip_waypoints"] = "true" if request.skip_waypoints else "false"
    return params
```

### 3.4 Merge general options into all four client methods

In each of `get_route()`, `get_matrix()`, `match_trace()`, and `get_trip()`, after building the base params dict, add:

```python
params.update(self._serialize_common_options(request))
```

Also update `get_nearest()` to call `_serialize_common_options` on the `NearestRequest`.

### 3.5 VRP internal call compatibility

`VrpService` constructs `MatrixRequest` and `TripRequest` without general option fields. All `CommonRoutingOptions` fields default to `None`, which causes `_serialize_common_options` to return an empty dict. No changes to `vrp_service.py`.

---

## Phase 4 — Tile Proxy and Error Handling Improvement

**Priority:** Low
**Files changed:** `app/main.py`, `app/services/osrm_client.py`
**Risk:** Low for tile proxy. The error handling improvement is technically a breaking change for callers who match on the string `"Routing service error"` — document in CHANGELOG.

### Rationale

The Tile service is the only remaining missing OSRM service after Phase 2. It returns binary Mapbox Vector Tile (MVT) data, which requires a different response path from the JSON-returning `_get()` helper. The error-handling improvement is grouped here because both changes touch the same two files without modifying schemas.

### 4.1 Add `get_tile()` to `OSRMClient`

The existing `_get()` helper calls `response.json()`. Tile responses are binary, so a separate raw-bytes method is needed:

```python
async def get_tile(self, profile: str, z: int, x: int, y: int) -> bytes:
    """Fetch a Mapbox Vector Tile from OSRM and return raw bytes."""
    endpoint = f"/tile/v1/{profile}/tile({x},{y},{z}).mvt"
    response = await self._client.get(endpoint)
    if response.is_error:
        logger.error("OSRM tile error at %s: status=%s", response.url, response.status_code)
    response.raise_for_status()
    return response.content
```

### 4.2 Add tile proxy endpoint to `app/main.py`

Add `from fastapi.responses import Response` to imports.

```python
@app.get(
    "/tile/{profile}/{z}/{x}/{y}.mvt",
    tags=["Tiles"],
    summary="Proxy OSRM Vector Tile",
    response_class=Response
)
@limiter.limit("600/minute")
async def get_tile(request: Request, profile: str, z: int, x: int, y: int):
    """
    Proxy a Mapbox Vector Tile from the OSRM tile service.
    Returns binary protobuf content (application/x-protobuf).
    Minimum zoom level supported by OSRM is 12.
    """
    try:
        tile_bytes = await osrm_client.get_tile(profile, z, x, y)
        return Response(content=tile_bytes, media_type="application/x-protobuf")
    except httpx.HTTPStatusError as e:
        logger.error("OSRM HTTP error on /tile: status=%s", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail="Tile service error")
    except Exception:
        logger.exception("Unexpected error on /tile")
        raise HTTPException(status_code=500, detail="Internal server error")
```

Note: The URL pattern `/tile/{profile}/{z}/{x}/{y}.mvt` differs from the OSRM canonical form `/tile/v1/{profile}/tile({x},{y},{z}).mvt` because FastAPI path parameters cannot contain parentheses. `get_tile()` in the client constructs the correct upstream URL.

### 4.3 Improve OSRM error body passthrough

Currently all `httpx.HTTPStatusError` blocks in `main.py` discard the upstream error body. OSRM JSON error responses contain `code` (e.g., `"NoRoute"`, `"InvalidValue"`, `"TooBig"`) and `message`. These should be forwarded to callers.

Add a module-level helper function in `main.py` before the endpoint definitions:

```python
def _parse_osrm_error(e: httpx.HTTPStatusError):
    """Extract structured error detail from an OSRM error response."""
    try:
        body = e.response.json()
        return {"code": body.get("code", "Error"), "message": body.get("message", "Routing service error")}
    except Exception:
        return "Routing service error"
```

Replace the generic error pattern in all endpoint handlers:

```python
# Before (in each endpoint)
raise HTTPException(status_code=e.response.status_code, detail="Routing service error")

# After
raise HTTPException(status_code=e.response.status_code, detail=_parse_osrm_error(e))
```

Apply to all HTTP-error catch blocks: `/route`, `/matrix`, `/matrix-graph`, `/match`, `/trip`, `/nearest`, `/tile`.

---

## Implementation Sequencing and Dependencies

The phases are ordered so that each is independently deployable:

1. **Phase 1** must be completed first — it changes the `get_route()` method signature. The call site in `main.py` must be updated atomically with the schema and client changes.
2. **Phase 2** is fully independent. Entirely new code paths, no existing lines modified.
3. **Phase 3** requires Phase 1 to be complete, because `CommonRoutingOptions` is added as a base class to the four schemas already modified in Phase 1. Do not begin Phase 3 before Phase 1 is merged.
4. **Phase 4** is independent of Phases 2 and 3. The `_parse_osrm_error` helper can be introduced at any time. The tile endpoint can be added before or after Phase 2.

Safe order within a single sprint: **Phase 1 → Phase 2 + Phase 4 in parallel → Phase 3**.

---

## Backwards Compatibility Notes

| Change | Compatibility impact |
|---|---|
| New schema fields with defaults | Non-breaking — all defaults reproduce current hardcoded behaviour |
| `get_route()` signature change | One call site in `main.py`, must change atomically |
| `VrpService` internal `MatrixRequest`/`TripRequest` construction | Unaffected — all new fields have defaults matching current values |
| Error response body change (Phase 4) | **Potentially breaking** for callers matching on `"Routing service error"` string |
| New `/nearest` endpoint | Non-breaking — additive |
| New `/tile/...` endpoint | Non-breaking — additive |

---

## Critical Files for Implementation

| File | Phases | Change type |
|---|---|---|
| `app/models/schemas.py` | 1, 2, 3 | New fields, new schemas, new base class |
| `app/services/osrm_client.py` | 1, 2, 3, 4 | Method updates, new methods, new helper |
| `app/main.py` | 1, 2, 4 | Call site update, new endpoints, error helper |
| `app/config.py` | 2 | New `RATE_LIMIT_NEAREST` constant |
| `app/services/vrp_service.py` | — | No changes required across all phases |
