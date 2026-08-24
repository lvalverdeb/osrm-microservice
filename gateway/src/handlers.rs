//! HTTP handlers, one per endpoint of `src/app/main.py`.

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};

use crate::config::Settings;
use crate::error::{parse_body, ApiError};
use crate::models::{
    join_coordinates, MatchRequest, MatrixRequest, NearestRequest, RouteRequest, TripRequest,
    Validate, VrpRequest,
};
use crate::osrm::client::OsrmClient;
use crate::osrm::params;
use crate::vrp::solve::{self, VehicleRoute, VrpAllocationResponse, VrpResponse};

#[derive(Clone)]
pub struct AppState {
    pub client: Arc<OsrmClient>,
    pub settings: Arc<Settings>,
    /// Bounds concurrent solves. The schema cap bounds one request; this bounds
    /// how many run at once, because peak memory is the product of the two --
    /// one 2000-stop solve peaked near 277 MB and four together reached 615 MB
    /// on a 2 GB host.
    pub vrp_slots: Arc<tokio::sync::Semaphore>,
    pub limiter: Arc<crate::ratelimit::RateLimiter>,
    pub limits: crate::ratelimit::Limits,
    pub trusted_proxies: Arc<crate::ratelimit::TrustedProxies>,
    pub metrics: Arc<crate::metrics::Metrics>,
}

/// Decode and validate a request body in one step.
fn accept<T: for<'de> serde::Deserialize<'de> + Validate>(body: &[u8]) -> Result<T, ApiError> {
    let request: T = parse_body(body)?;
    let errors = request.validate();
    if errors.is_empty() {
        Ok(request)
    } else {
        Err(ApiError::Validation(errors))
    }
}

#[utoipa::path(
    post,
    path = "/route",
    tag = "Routing",
    request_body = RouteRequest,
    responses(
        (status = 200, description = "Calculate a route through an ordered list of points.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
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
    path = "/matrix",
    tag = "Routing",
    request_body = MatrixRequest,
    responses(
        (status = 200, description = "Duration and distance matrix between coordinates.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
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
    path = "/matrix-graph",
    tag = "Routing",
    request_body = MatrixRequest,
    responses(
        (status = 200, description = "The same matrix, returned as a node-link graph.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
    )
)]
pub async fn matrix_graph(State(state): State<AppState>, body: Bytes)
    -> Result<Response, ApiError> {
    let request: MatrixRequest = accept(&body)?;
    check_budget(&state, &request)?;
    let (endpoint, params) = matrix_call(&request);
    // The one endpoint that computes on the matrix rather than relaying it.
    let data = state.client.get_json(&endpoint, &params).await?;
    Ok(Json(build_graph(&data, &request)).into_response())
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
fn build_graph(data: &Value, request: &MatrixRequest) -> Value {
    let durations = data.get("durations").and_then(Value::as_array).cloned().unwrap_or_default();
    let distances = data.get("distances").and_then(Value::as_array).cloned().unwrap_or_default();

    let nodes: Vec<Value> = request.coordinates.iter().enumerate()
        .map(|(index, coordinate)| json!({
            "lon": coordinate.longitude, "lat": coordinate.latitude, "id": index
        }))
        .collect();

    let mut edges = Vec::new();
    for (i, row) in durations.iter().filter_map(Value::as_array).enumerate() {
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
    json!({"directed": true, "multigraph": false, "graph": {}, "nodes": nodes, "edges": edges})
}

#[utoipa::path(
    post,
    path = "/match",
    tag = "Routing",
    request_body = MatchRequest,
    responses(
        (status = 200, description = "Snap a noisy GPS trace to the road network.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
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
    path = "/trip",
    tag = "Routing",
    request_body = TripRequest,
    responses(
        (status = 200, description = "Optimise the visiting order of a set of coordinates.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
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
    path = "/nearest",
    tag = "Routing",
    request_body = NearestRequest,
    responses(
        (status = 200, description = "Snap a coordinate to the nearest road segment.", body = Object),
        (status = 422, description = "Request failed validation"),
        (status = 429, description = "Rate limit exceeded"),
    )
)]
pub async fn nearest(State(state): State<AppState>, body: Bytes) -> Result<Response, ApiError> {
    let request: NearestRequest = accept(&body)?;
    let endpoint = format!("/nearest/v1/{}/{}", request.profile.as_str(),
                           request.coordinate.as_pair());
    Ok(proxy(state.client.get(&endpoint, &params::nearest(&request)).await?))
}

/// Vector tiles: raw protobuf, no cache, no retry.
#[utoipa::path(
    get,
    path = "/tile/{profile}/{z}/{x}/{y}",
    tag = "Routing",
    params(
        ("profile" = String, Path, description = "Routing profile"),
        ("z" = i64, Path, description = "Zoom level; OSRM serves tiles from 12 up"),
        ("x" = i64, Path, description = "Tile column"),
        ("y" = String, Path, description = "Tile row, with the .mvt suffix"),
    ),
    responses((status = 200, description = "Mapbox Vector Tile", content_type = "application/x-protobuf"))
)]
pub async fn tile(State(state): State<AppState>,
                  Path((profile, z, x, y)): Path<(String, i64, i64, String)>)
    -> Result<Response, ApiError> {
    // The route pattern captures `{y}.mvt` as one segment; strip the suffix.
    let y: i64 = y.trim_end_matches(".mvt").parse().unwrap_or_default();
    let bytes = state.client.get_tile(&profile, z, x, y).await?;
    Ok(([(header::CONTENT_TYPE, "application/x-protobuf")], bytes).into_response())
}

/// The OpenAPI schema, generated from the types that serve the requests.
///
/// See `crate::openapi` for why this is generated rather than served from a
/// snapshot of FastAPI's output.
pub async fn openapi() -> Response {
    // Embedded rather than read at runtime, so the binary stays a single file
    // and neither deployment path grows an extra artifact to install.
    ([(header::CONTENT_TYPE, "application/json")], crate::openapi::document()).into_response()
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
        let graph = build_graph(&data, &request());
        assert_eq!(graph, json!({
            "directed": true, "multigraph": false, "graph": {},
            "nodes": [{"lon": 1.0, "lat": 2.0, "id": 0}, {"lon": 3.0, "lat": 4.0, "id": 1}],
            "edges": [
                {"duration": 60, "distance": 600, "source": 0, "target": 1},
                {"duration": 60, "distance": 600, "source": 1, "target": 0}
            ]
        }));
    }

    #[test]
    fn graph_omits_distance_when_the_matrix_does_not_cover_the_cell() {
        let data = json!({"durations": [[0, 60], [60, 0]], "distances": []});
        let graph = build_graph(&data, &request());
        let edge = &graph["edges"][0];
        assert!(edge.get("distance").is_none());
        assert_eq!(edge["duration"], json!(60));
    }

    #[test]
    fn graph_has_no_self_edges() {
        let data = json!({"durations": [[0, 60], [60, 0]], "distances": [[0, 600], [600, 0]]});
        let graph = build_graph(&data, &request());
        assert_eq!(graph["edges"].as_array().unwrap().len(), 2);
    }
}


/// Wait for a solve slot, shedding rather than queueing indefinitely.
async fn vrp_slot(state: &AppState) -> Result<tokio::sync::OwnedSemaphorePermit, ApiError> {
    let wait = std::time::Duration::from_secs_f64(state.settings.vrp_queue_timeout);
    let slots = Arc::clone(&state.vrp_slots);
    match tokio::time::timeout(wait, slots.acquire_owned()).await {
        Ok(Ok(permit)) => Ok(permit),
        _ => Err(ApiError::CapacityExhausted {
            retry_after: (state.settings.vrp_queue_timeout as u64).max(1),
        }),
    }
}

/// Allocation options shared by both VRP endpoints.
async fn allocation(state: &AppState, request: &VrpRequest)
    -> Result<crate::vrp::allocate::Allocation, ApiError> {
    Ok(solve::allocation_for(&state.client, request, state.settings.matrix_batch_size,
                             state.settings.matrix_max_cells,
                             state.settings.vrp_sanity_limit_m).await?)
}

fn accept_vrp(state: &AppState, body: &[u8]) -> Result<VrpRequest, ApiError> {
    let request: VrpRequest = parse_body(body)?;
    let errors = request.validate_with(state.settings.vrp_max_stops);
    if errors.is_empty() {
        Ok(request)
    } else {
        Err(ApiError::Validation(errors))
    }
}

/// Allocate stops to depots, then solve one TSP per vehicle load.
#[utoipa::path(
    post,
    path = "/vrp",
    tag = "Optimisation",
    request_body = VrpRequest,
    responses(
        (status = 200, description = "Routes, one per vehicle load", body = VrpResponse),
        (status = 422, description = "Request failed validation"),
        (status = 503, description = "Optimisation capacity exhausted; retry after the given interval"),
    )
)]
pub async fn vrp(State(state): State<AppState>, body: Bytes) -> Result<Response, ApiError> {
    let request = accept_vrp(&state, &body)?;
    let _permit = vrp_slot(&state).await?;
    let allocated = allocation(&state, &request).await?;

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
                                                state.settings.vrp_chunk_concurrency).await?);
    }

    Ok(Json(VrpResponse::new(routes)).into_response())
}

/// The allocation phase alone, with caller IDs mapped back on.
#[utoipa::path(
    post,
    path = "/vrp/allocate",
    tag = "Optimisation",
    request_body = VrpRequest,
    responses(
        (status = 200, description = "Stops assigned to depots", body = VrpAllocationResponse),
        (status = 422, description = "Request failed validation"),
        (status = 503, description = "Optimisation capacity exhausted; retry after the given interval"),
    )
)]
pub async fn vrp_allocate(State(state): State<AppState>, body: Bytes)
    -> Result<Response, ApiError> {
    let request = accept_vrp(&state, &body)?;
    let _permit = vrp_slot(&state).await?;
    let allocated = allocation(&state, &request).await?;

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
