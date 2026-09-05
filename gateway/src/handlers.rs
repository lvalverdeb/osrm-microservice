//! HTTP handlers, one per endpoint of `src/app/main.py`.

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};
use tokio::sync::Semaphore;

use crate::config::Settings;
use crate::error::ApiError;
use crate::models::{
    join_coordinates, MatchRequest, MatrixRequest, NearestBatchRequest,
    NearestRequest, RouteRequest, TripRequest,
    Validate, ValidationError, VrpRequest,
};
use crate::osrm::client::{OsrmClient, OsrmError};
use crate::osrm::params;
use crate::vrp::solve::{self, VehicleRoute, VrpAllocationResponse, VrpResponse};

#[derive(Clone)]
pub struct AppState {
    pub client: Arc<OsrmClient>,
    pub settings: Arc<Settings>,
    /// Bounds concurrent solves and the queue for them. The schema cap bounds
    /// one request; this bounds how many run at once, because peak memory is
    /// the product of the two.
    pub vrp_gate: Arc<crate::admission::AdmissionGate>,
    pub limiter: Arc<crate::ratelimit::RateLimiter>,
    /// Shared with the OSRM client's L2 tier: one Redis, one connection.
    pub l2: Arc<crate::redis_cache::RedisCache>,
    pub limits: crate::ratelimit::Limits,
    pub trusted_proxies: Arc<crate::ratelimit::TrustedProxies>,
    pub metrics: Arc<crate::metrics::Metrics>,
}

/// Decode and validate a request body in one step.
fn accept<T: for<'de> serde::Deserialize<'de> + Validate>(body: &[u8]) -> Result<T, ApiError> {
    finish(crate::error::parse_collecting::<T>(body)?, |request| request.validate())
}

/// Merge decode failures with the model's own, as pydantic reports them together.
///
/// A body that is both the wrong type in one field and out of range in another
/// produced one error here and two there, because validation never ran once
/// decoding had failed. Decoding now yields a patched value, so the model can be
/// validated in the same pass.
fn finish<T>(parsed: crate::error::Parsed<T>,
             validate: impl FnOnce(&T) -> Vec<crate::models::ValidationError>)
    -> Result<T, ApiError> {
    let Some(request) = parsed.value else {
        let mut errors = parsed.errors;
        crate::models::fill_inputs(&mut errors, &parsed.document);
        return Err(ApiError::Validation(errors));
    };
    let mut errors = validate(&request);
    // A constraint error against a patched position describes the placeholder,
    // not the request, and reporting it would invent a failure the caller never
    // caused.
    errors.retain(|error| {
        let path: Vec<_> = error.loc.iter().skip(1).cloned().collect();
        !parsed.patched.iter().any(|patched| path.starts_with(patched))
    });
    if parsed.errors.is_empty() && errors.is_empty() {
        return Ok(request);
    }
    // Constraint failures first: pydantic walks fields in declaration order and
    // this is the order it produced for every mixed body checked against it.
    errors.extend(parsed.errors);
    crate::models::fill_inputs(&mut errors, &parsed.document);
    Err(ApiError::Validation(errors))
}

#[utoipa::path(
    post,
    path = "/v1/route",
    tag = "Routing",
    request_body = RouteRequest,
    responses(
        (status = 200, description = "Calculate a route through an ordered list of points.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn route(State(state): State<AppState>, body: Bytes) -> Result<Response, ApiError> {
    let request: RouteRequest = accept(&body)?;
    let coordinates = request.coordinates();
    let endpoint = format!("/route/v1/{}/{}", request.profile.as_str(),
                           join_coordinates(&coordinates));
    let params = params::route(&request, coordinates.len());
    Ok(proxy(state.client.get(&endpoint, &params).await?))
}

#[utoipa::path(
    post,
    path = "/v1/matrix",
    tag = "Routing",
    description = "Note a constraint this schema cannot express: `sources` x \
`destinations` must not exceed MATRIX_MAX_CELLS, counting an omitted or empty \
list as every coordinate. Exceeding it is a 422 naming the limit.",
    request_body = MatrixRequest,
    responses(
        (status = 200, description = "Duration and distance matrix between coordinates.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn matrix(State(state): State<AppState>, body: Bytes) -> Result<Response, ApiError> {
    let request: MatrixRequest = accept(&body)?;
    check_budget(&state, &request)?;
    let (endpoint, params) = matrix_call(&request);
    Ok(proxy(state.client.get(&endpoint, &params).await?))
}

/// The same upstream call as `/matrix`, reshaped into a node-link graph.
#[utoipa::path(
    post,
    path = "/v1/matrix-graph",
    tag = "Routing",
    description = "Note a constraint this schema cannot express: `sources` x \
`destinations` must not exceed MATRIX_MAX_CELLS, counting an omitted or empty \
list as every coordinate. Exceeding it is a 422 naming the limit.",
    request_body = MatrixRequest,
    responses(
        (status = 200, description = "The same matrix, returned as a node-link graph.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn matrix_graph(State(state): State<AppState>, body: Bytes)
    -> Result<Response, ApiError> {
    let request: MatrixRequest = accept(&body)?;
    check_budget(&state, &request)?;
    let (endpoint, params) = matrix_call(&request);
    // The one endpoint that computes on the matrix rather than relaying it.
    let data = state.client.get_json(&endpoint, &params).await?;
    Ok(Json(build_graph(&data, &request)?).into_response())
}

/// Relay an upstream body verbatim.
///
/// No decode, no re-encode: the bytes the engine sent are the bytes the caller
/// gets, which is both faster and the only way to guarantee the numbers are
/// unchanged.
fn proxy(body: Arc<Vec<u8>>) -> Response {
    ([(header::CONTENT_TYPE, "application/json")], body.to_vec()).into_response()
}

/// Reject a matrix request that would exceed the engine's cell budget.
fn check_budget(state: &AppState, request: &MatrixRequest) -> Result<(), ApiError> {
    let budget = request.validate_budget(state.settings.matrix_max_cells);
    if budget.is_empty() {
        Ok(())
    } else {
        Err(ApiError::Validation(budget))
    }
}

/// The upstream call `/matrix` and `/matrix-graph` share.
fn matrix_call(request: &MatrixRequest) -> (String, crate::cache::Params) {
    let endpoint = format!("/table/v1/{}/{}", request.profile.as_str(),
                           join_coordinates(&request.coordinates));
    (endpoint, params::matrix(request))
}

/// Reproduce `networkx.node_link_data` for a dense directed graph.
///
/// The edge list key is `"edges"`, which is what networkx 3.6 emits; releases
/// before 3.4 emitted `"links"`. The Python handler passes `node_link_data`
/// through unchanged, so this endpoint's public shape follows the resolved
/// library version rather than any code in that repo -- worth pinning here.
fn build_graph(data: &Value, request: &MatrixRequest) -> Result<Value, OsrmError> {
    let durations = data.get("durations").and_then(Value::as_array).cloned().unwrap_or_default();
    let distances = data.get("distances").and_then(Value::as_array).cloned().unwrap_or_default();

    let mut nodes: Vec<Value> = request.coordinates.iter().enumerate()
        .map(|(index, coordinate)| json!({
            "lon": coordinate.longitude, "lat": coordinate.latitude, "id": index
        }))
        .collect();
    // `G.add_edge(i, j)` creates a node on demand, so a matrix wider or taller
    // than the coordinate list leaves bare `{"id": i}` nodes in networkx's
    // output. Deriving nodes only from the coordinates dropped them.
    let mut implied: Vec<usize> = Vec::new();
    let note = |index: usize, implied: &mut Vec<usize>| {
        if index >= request.coordinates.len() && !implied.contains(&index) {
            implied.push(index);
        }
    };
    for (i, row) in durations.iter().enumerate() {
        let Some(row) = row.as_array() else { continue };
        for j in 0..row.len() {
            if i != j {
                note(i, &mut implied);
                note(j, &mut implied);
            }
        }
    }
    nodes.extend(implied.into_iter().map(|index| json!({ "id": index })));

    let mut edges = Vec::new();
    // Enumerate before filtering. `filter_map(..).enumerate()` renumbered every
    // row after a non-array one, so a single null row shifted each subsequent
    // edge's `source` down by one and the endpoint answered 200 with a
    // silently mislabelled graph. Python indexed by position and raised
    // TypeError on such a row, which surfaced as 500.
    for (i, row) in durations.iter().enumerate() {
        let Some(row) = row.as_array() else {
            return Err(OsrmError::Unavailable(
                format!("upstream durations row {i} is not an array")));
        };
        for (j, duration) in row.iter().enumerate() {
            if i == j {
                continue;
            }
            let mut edge = Map::new();
            edge.insert("duration".into(), duration.clone());
            if let Some(distance) = distances.get(i).and_then(Value::as_array).and_then(|r| r.get(j)) {
                edge.insert("distance".into(), distance.clone());
            }
            edge.insert("source".into(), json!(i));
            edge.insert("target".into(), json!(j));
            edges.push(Value::Object(edge));
        }
    }
    Ok(json!({"directed": true, "multigraph": false, "graph": {}, "nodes": nodes, "edges": edges}))
}

#[utoipa::path(
    post,
    path = "/v1/match",
    tag = "Routing",
    request_body = MatchRequest,
    responses(
        (status = 200, description = "Snap a noisy GPS trace to the road network.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn match_trace(State(state): State<AppState>, body: Bytes)
    -> Result<Response, ApiError> {
    let request: MatchRequest = accept(&body)?;
    let endpoint = format!("/match/v1/{}/{}", request.profile.as_str(),
                           join_coordinates(&request.coordinates()));
    Ok(proxy(state.client.get(&endpoint, &params::match_trace(&request)).await?))
}

#[utoipa::path(
    post,
    path = "/v1/trip",
    tag = "Routing",
    request_body = TripRequest,
    responses(
        (status = 200, description = "Optimise the visiting order of a set of coordinates.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn trip(State(state): State<AppState>, body: Bytes) -> Result<Response, ApiError> {
    let request: TripRequest = accept(&body)?;
    let endpoint = format!("/trip/v1/{}/{}", request.profile.as_str(),
                           join_coordinates(&request.coordinates));
    Ok(proxy(state.client.get(&endpoint, &params::trip(&request)).await?))
}

#[utoipa::path(
    post,
    path = "/v1/nearest",
    tag = "Routing",
    request_body = NearestRequest,
    responses(
        (status = 200, description = "Snap a coordinate to the nearest road segment.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn nearest(State(state): State<AppState>, body: Bytes) -> Result<Response, ApiError> {
    let request: NearestRequest = accept(&body)?;
    let endpoint = format!("/nearest/v1/{}/{}", request.profile.as_str(),
                           request.coordinate.as_pair());
    Ok(proxy(state.client.get(&endpoint, &params::nearest(&request)).await?))
}

#[utoipa::path(
    post,
    path = "/v1/nearest/batch",
    tag = "Routing",
    request_body = NearestBatchRequest,
    responses(
        (status = 200, description = "Snap every coordinate, in the order given.", body = Object),
        (status = 422, description = "Request failed validation, or exceeded the batch limit"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn nearest_batch(State(state): State<AppState>, body: Bytes)
    -> Result<Response, ApiError> {
    let request: NearestBatchRequest = accept(&body)?;
    let budget = request.validate_budget(state.settings.nearest_max_coordinates);
    if !budget.is_empty() {
        return Err(ApiError::Validation(budget));
    }

    let params = params::nearest_batch(&request);
    let profile = request.profile.as_str().to_string();
    let slots = Arc::new(Semaphore::new(state.settings.nearest_batch_concurrency.max(1)));
    let mut set = tokio::task::JoinSet::new();
    for (position, coordinate) in request.coordinates.iter().enumerate() {
        let endpoint = format!("/nearest/v1/{}/{}", profile, coordinate.as_pair());
        let (client, slots, params) = (Arc::clone(&state.client), Arc::clone(&slots),
                                       params.clone());
        set.spawn(async move {
            let _permit = slots.acquire().await.expect("semaphore is never closed");
            // Through the same `get_json` as the single endpoint, so a batch
            // fills and reuses the very cache entries `/nearest` would.
            (position, client.get_json(&endpoint, &params).await)
        });
    }

    let mut snapped: Vec<Option<Value>> = vec![None; request.coordinates.len()];
    while let Some(joined) = set.join_next().await {
        let (position, result) = joined.map_err(|e| ApiError::Upstream(
            OsrmError::Unavailable(format!("batch nearest task failed: {e}"))))?;
        match result {
            Ok(value) => snapped[position] = Some(value),
            Err(error) => {
                // One unreachable coordinate makes the whole answer untrustworthy
                // for the caller's purpose -- gating geocodes -- so stop rather
                // than return a list with a hole nobody looks for. The same
                // choice `solve_depot_routes` makes.
                set.abort_all();
                return Err(ApiError::Upstream(error));
            }
        }
    }

    let results: Vec<Value> = snapped.into_iter().flatten().collect();
    Ok(Json(serde_json::json!({ "code": "Ok", "results": results })).into_response())
}

/// Vector tiles: raw protobuf, no cache, no retry.
#[utoipa::path(
    get,
    path = "/v1/tile/{profile}/{z}/{x}/{y}",
    tag = "Routing",
    params(
        ("profile" = String, Path, description = "Routing profile"),
        ("z" = i64, Path, description = "Zoom level; OSRM serves tiles from 12 up"),
        ("x" = i64, Path, description = "Tile column"),
        ("y" = String, Path, description = "Tile row, with the .mvt suffix"),
    ),
    responses(
        (status = 200, description = "Mapbox Vector Tile", content_type = "application/x-protobuf"),
        (status = 500, description = "The routing engine could not be reached"),
        (status = "default", description = "An error from the routing engine, relayed with its own status"),
    )
)]
pub async fn tile(State(state): State<AppState>,
                  Path((profile, z, x, y)): Path<(String, String, String, String)>)
    -> Result<Response, ApiError> {
    // Extracted as strings and parsed here. Typing them `i64` handed rejection
    // to axum, whose `PathRejection` is a 400 with a plain-text body, where
    // FastAPI's `z: int` produced the same 422 JSON as any other bad input.
    let bytes = state.client
        .get_tile(&profile, tile_int(&z, "z")?, tile_int(&x, "x")?, tile_row(&y)?)
        .await?;
    Ok(([(header::CONTENT_TYPE, "application/x-protobuf")], bytes).into_response())
}

/// Decode the final path segment of a tile request.
///
/// FastAPI routed `/tile/{profile}/{z}/{x}/{y}.mvt` with `y: int`, so a segment
/// without the suffix never matched the route at all (404) and a non-integer
/// row was a 422. axum captures the whole segment, so both checks live here.
///
/// This was `trim_end_matches(".mvt").parse().unwrap_or_default()`, which
/// collapsed every malformed row to **0** and answered 200 with a real tile
/// from the wrong place -- the failure mode a caller cannot detect.
fn tile_row(segment: &str) -> Result<i64, ApiError> {
    let row = segment.strip_suffix(".mvt").ok_or(ApiError::NotFound)?;
    tile_int(row, "y")
}

/// Parse one integer tile path parameter, reporting failure as pydantic does.
fn tile_int(raw: &str, name: &str) -> Result<i64, ApiError> {
    raw.parse().map_err(|_| ApiError::Validation(vec![
        ValidationError::path_param("int_parsing", name,
            "Input should be a valid integer, unable to parse string as an integer", raw)
    ]))
}

/// The OpenAPI schema, generated from the types that serve the requests.
///
/// See `crate::openapi` for why this is generated rather than served from a
/// snapshot of FastAPI's output.
pub async fn openapi(State(state): State<AppState>) -> Response {
    // Embedded rather than read at runtime, so the binary stays a single file
    // and neither deployment path grows an extra artifact to install.
    ([(header::CONTENT_TYPE, "application/json")], crate::openapi::document(state.settings.vrp_max_stops)).into_response()
}

/// The interactive docs page.
///
/// Mirrors what FastAPI serves: a thin HTML shell that loads Swagger UI from a
/// CDN and points it at `/openapi.json`. FastAPI does the same rather than
/// vendoring the assets, so this needs no embedded bundle either.
pub async fn docs(State(state): State<AppState>) -> Response {
    let title = &state.settings.app_name;
    let html = format!(r#"<!DOCTYPE html>
<html>
  <head>
    <title>{title} - Swagger UI</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>SwaggerUIBundle({{url: '/openapi.json', dom_id: '#swagger-ui'}})</script>
  </body>
</html>"#);
    ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], html).into_response()
}

/// ReDoc, which FastAPI served at this path by default.
///
/// Dropping it was a silent 404 for anyone whose bookmark or internal docs
/// link pointed here; the schema behind it is the same `/openapi.json`.
pub async fn redoc(State(state): State<AppState>) -> Response {
    let title = &state.settings.app_name;
    let html = format!(r#"<!DOCTYPE html>
<html>
  <head>
    <title>{title} - ReDoc</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
  </head>
  <body>
    <redoc spec-url="/openapi.json"></redoc>
    <script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"></script>
  </body>
</html>"#);
    ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], html).into_response()
}

/// Swagger UI's OAuth2 redirect target, which FastAPI registers with `/docs`.
///
/// No OAuth flow is configured on either side, so this only ever runs if a
/// reader points their own Swagger UI at this schema; it exists because the
/// route existed, not because the gateway authenticates anything.
pub async fn oauth2_redirect() -> Response {
    let html = r#"<!DOCTYPE html>
<html>
<head><title>Swagger UI: OAuth2 Redirect</title></head>
<body>
<script>
'use strict';
function run () {
    var oauth2 = window.opener.swaggerUIRedirectOauth2;
    var sentState = oauth2.state;
    var redirectUrl = oauth2.redirectUrl;
    var isValid, qp, arr;
    if (/code|token|error/.test(window.location.hash)) {
        qp = window.location.hash.substring(1).replace('?', '&');
    } else {
        qp = location.search.substring(1);
    }
    arr = qp.split('&');
    arr.forEach(function (v, i, _arr) { _arr[i] = '"' + v.replace('=', '":"') + '"'; });
    qp = qp ? JSON.parse('{' + arr.join() + '}', function (key, value) {
        return key === '' ? value : decodeURIComponent(value);
    }) : {};
    isValid = qp.state === sentState;
    if ((oauth2.auth.schema.get('flow') === 'accessCode' ||
         oauth2.auth.schema.get('flow') === 'authorizationCode' ||
         oauth2.auth.schema.get('flow') === 'authorization_code') && !oauth2.auth.code) {
        if (!isValid) {
            oauth2.errCb({authId: oauth2.auth.name, source: 'auth', level: 'warning',
                message: 'Authorization may be unsafe, passed state was changed in server. '
                       + 'The passed state wasn't returned from auth server.'});
        }
        if (qp.code) {
            delete oauth2.state;
            oauth2.auth.code = qp.code;
            oauth2.callback({auth: oauth2.auth, redirectUrl: redirectUrl});
        } else {
            let oauthErrorMsg = qp.error === 'server_error' ? 'Server error.'
                : qp.error === 'temporarily_unavailable' ? 'Temporarily unavailable.' : null;
            oauth2.errCb({authId: oauth2.auth.name, source: 'auth', level: 'error',
                message: oauthErrorMsg || 'Authorization failed: no accessCode received.'});
        }
    } else {
        oauth2.callback({auth: oauth2.auth, token: qp, isValid: isValid,
                         redirectUrl: redirectUrl});
    }
    window.close();
}
if (document.readyState !== 'loading') { run(); }
else { document.addEventListener('DOMContentLoaded', function () { run(); }); }
</script>
</body>
</html>"#;
    ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], html).into_response()
}

/// Starlette's 404 body, which axum's default fallback does not send.
pub async fn not_found() -> Response {
    ApiError::NotFound.into_response()
}

/// Starlette's 405 body, likewise.
pub async fn method_not_allowed() -> Response {
    (StatusCode::METHOD_NOT_ALLOWED,
     Json(json!({ "detail": "Method Not Allowed" }))).into_response()
}

/// Prometheus exposition. Never rate limited, so a scrape is never shed.
#[utoipa::path(
    get, path = "/metrics", tag = "Infrastructure",
    responses((status = 200, description = "Prometheus exposition", content_type = "text/plain"))
)]
pub async fn metrics(State(state): State<AppState>) -> Response {
    ([(header::CONTENT_TYPE, "text/plain; version=0.0.4; charset=utf-8")],
     state.metrics.encode()).into_response()
}

/// Always 200, even when the engine is down.
///
/// A dashboard reads the body for detail; `/ready` is what a balancer probes,
/// and both deployment health checks use that instead.
#[utoipa::path(
    get, path = "/health", tag = "Infrastructure",
    responses((status = 200, description = "Always 200, even when the engine is down; read the body for detail", body = Object))
)]
pub async fn health(State(state): State<AppState>) -> Json<Value> {
    let up = state.client.ping().await;
    Json(json!({
        "status": if up { "healthy" } else { "degraded" },
        "service": state.settings.app_name,
        "osrm_backend": if up { "up" } else { "down" },
    }))
}

/// 503 when the engine is down, so a balancer drains this node.
#[utoipa::path(
    get, path = "/ready", tag = "Infrastructure",
    responses(
        (status = 200, description = "Engine reachable", body = Object),
        (status = 503, description = "Engine unreachable; a balancer should drain this node"),
    )
)]
pub async fn ready(State(state): State<AppState>) -> (StatusCode, Json<Value>) {
    let up = state.client.ping().await;
    let body = json!({
        "status": if up { "ready" } else { "not_ready" },
        "service": state.settings.app_name,
        "osrm_backend": if up { "up" } else { "down" },
    });
    let code = if up { StatusCode::OK } else { StatusCode::SERVICE_UNAVAILABLE };
    (code, Json(body))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> MatrixRequest {
        serde_json::from_str(
            r#"{"coordinates":[{"longitude":1.0,"latitude":2.0},
                               {"longitude":3.0,"latitude":4.0}]}"#).unwrap()
    }

    /// Pinned against real `nx.node_link_data` output for the same input.
    #[test]
    fn graph_matches_networkx_node_link_data() {
        let data = json!({"durations": [[0, 60], [60, 0]], "distances": [[0, 600], [600, 0]]});
        let graph = build_graph(&data, &request()).expect("well-formed matrix");
        assert_eq!(graph, json!({
            "directed": true, "multigraph": false, "graph": {},
            "nodes": [{"lon": 1.0, "lat": 2.0, "id": 0}, {"lon": 3.0, "lat": 4.0, "id": 1}],
            "edges": [
                {"duration": 60, "distance": 600, "source": 0, "target": 1},
                {"duration": 60, "distance": 600, "source": 1, "target": 0}
            ]
        }));
    }

    /// A null row must not renumber the rows after it.
    ///
    /// `filter_map(..).enumerate()` dropped the row and shifted every later
    /// `source` down by one, answering 200 with a mislabelled graph. Python
    /// indexed by position and raised TypeError, which surfaced as 500.
    #[test]
    fn a_null_durations_row_is_refused_rather_than_renumbering_the_rest() {
        let data = json!({"durations": [null, [60, 0]], "distances": []});
        assert!(build_graph(&data, &request()).is_err());
    }

    #[test]
    fn tile_row_requires_the_mvt_suffix_and_an_integer() {
        assert_eq!(tile_row("200.mvt").expect("a well-formed row"), 200);
        // Previously y=0 plus a 200 response, for each of these.
        assert!(matches!(tile_row("200"), Err(ApiError::NotFound)));
        assert!(matches!(tile_row("200.png"), Err(ApiError::NotFound)));
        assert!(matches!(tile_row("abc.mvt"), Err(ApiError::Validation(_))));
        assert!(matches!(tile_row(".mvt"), Err(ApiError::Validation(_))));
        // z and x were left to axum's Path rejection: a 400 with a plain-text
        // body, where FastAPI's `z: int` gave the same 422 JSON as `y`.
        assert_eq!(tile_int("12", "z").expect("a valid zoom"), 12);
        assert!(matches!(tile_int("zz", "z"), Err(ApiError::Validation(_))));
        assert!(matches!(tile_int("", "x"), Err(ApiError::Validation(_))));
    }

    #[test]
    fn graph_omits_distance_when_the_matrix_does_not_cover_the_cell() {
        let data = json!({"durations": [[0, 60], [60, 0]], "distances": []});
        let graph = build_graph(&data, &request()).expect("well-formed matrix");
        let edge = &graph["edges"][0];
        assert!(edge.get("distance").is_none());
        assert_eq!(edge["duration"], json!(60));
    }

    #[test]
    fn graph_has_no_self_edges() {
        let data = json!({"durations": [[0, 60], [60, 0]], "distances": [[0, 600], [600, 0]]});
        let graph = build_graph(&data, &request()).expect("well-formed matrix");
        assert_eq!(graph["edges"].as_array().unwrap().len(), 2);
    }
}


/// Take a solve slot, or shed.
///
/// Both refusals answer 503 with the same `Retry-After`: a caller can do nothing
/// different with "the queue was full" than with "the queue did not drain in
/// time". The distinction shows up in latency, which is the point -- a
/// depth refusal returns in microseconds where a timeout costs the full wait.
async fn vrp_slot(state: &AppState) -> Result<tokio::sync::OwnedSemaphorePermit, ApiError> {
    state.vrp_gate.enter().await.map_err(|_| ApiError::CapacityExhausted {
        retry_after: (state.settings.vrp_queue_timeout as u64).max(1),
    })
}

/// Allocation options shared by both VRP endpoints.
async fn allocation(state: &AppState, request: &VrpRequest)
    -> Result<crate::vrp::allocate::Allocation, ApiError> {
    Ok(solve::allocation_for(&state.client, request, state.settings.matrix_batch_size,
                             state.settings.matrix_max_cells,
                             state.settings.vrp_sanity_limit_m).await?)
}

fn accept_vrp(state: &AppState, body: &[u8]) -> Result<VrpRequest, ApiError> {
    let max_stops = state.settings.vrp_max_stops;
    finish(crate::error::parse_collecting::<VrpRequest>(body)?,
           |request| request.validate_with(max_stops))
}

/// Allocate stops to depots, then solve one TSP per vehicle load.
#[utoipa::path(
    post,
    path = "/v1/vrp",
    tag = "Optimisation",
    request_body = VrpRequest,
    responses(
        (status = 200, description = "Routes, one per vehicle load", body = VrpResponse),
        (status = 422, description = "Request failed validation"),
        (status = 503, description = "Optimisation capacity exhausted; retry after the given interval"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached, or a chunk solve failed"),
    )
)]
pub async fn vrp(State(state): State<AppState>, body: Bytes) -> Result<Response, ApiError> {
    let request = accept_vrp(&state, &body)?;
    let _permit = vrp_slot(&state).await?;
    let allocated = allocation(&state, &request).await.map_err(as_solve_failure)?;

    let mut routes: Vec<VehicleRoute> = Vec::new();
    // Depots are solved one at a time, as in Python; only the chunks within a
    // depot fan out. The offset is what numbers vehicles when depots carry no
    // IDs, so it must count routes built so far.
    for (depot_idx, stop_indices) in allocated.assignments.iter().enumerate() {
        if stop_indices.is_empty() {
            continue;
        }
        let chunks = solve::build_chunk_requests(&request, depot_idx, stop_indices,
                                                 routes.len(), state.settings.vrp_chunk_size);
        routes.extend(solve::solve_depot_routes(Arc::clone(&state.client), chunks,
                                                state.settings.vrp_chunk_concurrency)
                          .await.map_err(as_solve_failure)?);
    }

    Ok(Json(VrpResponse::new(routes)).into_response())
}

/// Collapse an engine failure during a solve to 500, as Python's handler does.
///
/// `/vrp` and `/vrp/allocate` are the two endpoints with no
/// `except httpx.HTTPStatusError` branch -- their bare `except Exception` turns
/// an upstream 400 into `500 {"detail":"Internal server error"}`, unlike the
/// other seven handlers which relay the engine's status. Reproduced here rather
/// than improved, so the two implementations answer the same thing.
///
/// A gateway-side refusal is not an engine failure and keeps its own status:
/// `RequestTooLong` has no counterpart in Python at all, so there is no
/// behaviour to match.
fn as_solve_failure(error: impl Into<ApiError>) -> ApiError {
    match error.into() {
        // A gateway-side refusal keeps its own status.
        upstream @ ApiError::Upstream(OsrmError::RequestTooLong { .. }) => upstream,
        ApiError::Upstream(other) => {
            ApiError::Upstream(OsrmError::Unavailable(format!("{other:?}")))
        }
        // Validation and capacity shedding are decided before the solve runs
        // and are not what the bare `except` was catching.
        other => other,
    }
}

/// The allocation phase alone, with caller IDs mapped back on.
#[utoipa::path(
    post,
    path = "/v1/vrp/allocate",
    tag = "Optimisation",
    request_body = VrpRequest,
    responses(
        (status = 200, description = "Stops assigned to depots", body = VrpAllocationResponse),
        (status = 422, description = "Request failed validation"),
        (status = 503, description = "Optimisation capacity exhausted; retry after the given interval"),
        (status = 429, description = "Rate limit exceeded"),
        (status = 500, description = "The routing engine could not be reached, or a chunk solve failed"),
    )
)]
pub async fn vrp_allocate(State(state): State<AppState>, body: Bytes)
    -> Result<Response, ApiError> {
    let request = accept_vrp(&state, &body)?;
    let _permit = vrp_slot(&state).await?;
    let allocated = allocation(&state, &request).await.map_err(as_solve_failure)?;

    let depot_ids: Vec<Option<Value>> = request.depots.iter().map(|d| d.id.clone()).collect();
    let stop_ids: Vec<Option<Value>> = request.stops.iter().map(|s| s.id.clone()).collect();
    let any_ids = depot_ids.iter().chain(stop_ids.iter())
        .any(|id| id.as_ref().is_some_and(|v| !v.is_null()));

    let mut allocations = Map::new();
    for (depot_idx, stop_indices) in allocated.assignments.iter().enumerate() {
        let key = if any_ids { label(depot_idx, &depot_ids) } else { depot_idx.to_string() };
        let values: Vec<Value> = stop_indices.iter()
            .map(|&i| if any_ids { id_or_index(i, &stop_ids) } else { Value::from(i) })
            .collect();
        allocations.insert(key, Value::Array(values));
    }
    let unreachable: Vec<Value> = allocated.unreachable.iter()
        .map(|&i| if any_ids { id_or_index(i, &stop_ids) } else { Value::from(i) })
        .collect();

    Ok(Json(VrpAllocationResponse {
        code: "Ok".to_string(),
        allocations,
        unreachable_stops: unreachable,
    })
    .into_response())
}

/// A caller-supplied ID, falling back to the raw index.
fn id_or_index(index: usize, ids: &[Option<Value>]) -> Value {
    match ids.get(index) {
        Some(Some(id)) if !id.is_null() => id.clone(),
        _ => Value::from(index),
    }
}

/// The same, rendered as a JSON object key.
fn label(index: usize, ids: &[Option<Value>]) -> String {
    match id_or_index(index, ids) {
        Value::String(text) => text,
        other => other.to_string(),
    }
}
