//! Evaluation spike: `/route` and `/matrix` reimplemented in Rust.
//!
//! This exists to answer one question with a measurement rather than an
//! inference: how much of a request does the gateway itself cost? It mirrors
//! the FastAPI gateway's request contract, upstream URL construction, and L1
//! cache semantics so that `loadtest/run.py --url` can drive either one.
//!
//! Deliberately absent, because each would otherwise flatter this side of the
//! comparison: rate limiting, Prometheus metrics, OpenTelemetry tracing, the
//! Redis L2 tier, retry/backoff, and the per-coordinate routing options
//! (`bearings`, `radiuses`, `hints`, `approaches`). See README.md.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, Router};
use moka::future::Cache;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

// ---------------------------------------------------------------------------
// Settings -- names and defaults mirror src/app/config.py
// ---------------------------------------------------------------------------

struct Settings {
    osrm_base_url: String,
    host: String,
    port: u16,
    l1_cache_ttl: u64,
    l1_cache_maxsize: u64,
    osrm_client_timeout: u64,
    matrix_max_cells: usize,
    worker_threads: usize,
    health_check_coords: String,
    health_check_timeout: u64,
}

impl Settings {
    fn from_env() -> Self {
        fn var<T: std::str::FromStr>(key: &str, fallback: T) -> T {
            std::env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(fallback)
        }
        Self {
            osrm_base_url: std::env::var("OSRM_BASE_URL")
                .unwrap_or_else(|_| "http://localhost:5000".to_string()),
            // HOST/PORT rather than a single BIND string, because that is what
            // both deployment paths already think in -- the compose service and
            // the rc.d script set them the same way they do for uvicorn.
            // Defaults to loopback; deployments set 0.0.0.0 explicitly.
            host: std::env::var("HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
            port: var("PORT", 8001u16),
            l1_cache_ttl: var("L1_CACHE_TTL", 900),
            l1_cache_maxsize: var("L1_CACHE_MAXSIZE", 1024),
            osrm_client_timeout: var("OSRM_CLIENT_TIMEOUT", 30),
            matrix_max_cells: var("MATRIX_MAX_CELLS", 10_000),
            // Defaults to 1 so a run matches a single uvicorn worker. Raise it
            // together with the Python side's WORKERS, never on its own.
            worker_threads: var("WORKERS", 1),
            health_check_coords: std::env::var("HEALTH_CHECK_COORDS")
                .unwrap_or_else(|_| "0,0;0,0".to_string()),
            health_check_timeout: var("HEALTH_CHECK_TIMEOUT", 2),
        }
    }
}

#[derive(Clone)]
struct AppState {
    http: reqwest::Client,
    cache: Cache<String, Arc<Value>>,
    base_url: Arc<String>,
    matrix_max_cells: usize,
    probe_path: Arc<String>,
    probe_timeout: Duration,
}

// ---------------------------------------------------------------------------
// Errors -- shaped like FastAPI's {"detail": ...}
// ---------------------------------------------------------------------------

struct ApiError(StatusCode, String);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({ "detail": self.1 }))).into_response()
    }
}

fn unprocessable(message: impl Into<String>) -> ApiError {
    ApiError(StatusCode::UNPROCESSABLE_ENTITY, message.into())
}

// ---------------------------------------------------------------------------
// Request models -- field names and defaults mirror app/models/schemas.py
// ---------------------------------------------------------------------------

#[derive(Deserialize, Clone, Copy)]
struct Coordinate {
    longitude: f64,
    latitude: f64,
}

impl Coordinate {
    fn validate(&self) -> Result<(), ApiError> {
        if !(-180.0..=180.0).contains(&self.longitude) {
            return Err(unprocessable("longitude must be between -180 and 180"));
        }
        if !(-90.0..=90.0).contains(&self.latitude) {
            return Err(unprocessable("latitude must be between -90 and 90"));
        }
        Ok(())
    }
}

fn d_driving() -> String { "driving".into() }
fn d_full() -> String { "full".into() }
fn d_geojson() -> String { "geojson".into() }
fn d_true() -> bool { true }
fn d_route_annotations() -> Option<String> { Some("distance,duration".into()) }
fn d_matrix_annotations() -> String { "duration,distance".into() }
fn d_alternatives() -> Value { Value::Bool(false) }

#[derive(Deserialize)]
struct RouteRequest {
    origin: Coordinate,
    destination: Coordinate,
    #[serde(default)]
    waypoints: Option<Vec<Coordinate>>,
    #[serde(default = "d_alternatives")]
    alternatives: Value,
    #[serde(default = "d_driving")]
    profile: String,
    #[serde(default = "d_full")]
    overview: String,
    #[serde(default = "d_geojson")]
    geometries: String,
    #[serde(default = "d_true")]
    steps: bool,
    #[serde(default = "d_route_annotations")]
    annotations: Option<String>,
    #[serde(default)]
    continue_straight: Option<String>,
}

#[derive(Deserialize)]
struct MatrixRequest {
    coordinates: Vec<Coordinate>,
    #[serde(default)]
    sources: Option<Vec<usize>>,
    #[serde(default)]
    destinations: Option<Vec<usize>>,
    #[serde(default = "d_driving")]
    profile: String,
    #[serde(default = "d_matrix_annotations")]
    annotations: String,
    #[serde(default)]
    fallback_speed: Option<f64>,
    #[serde(default)]
    fallback_coordinate: Option<String>,
    #[serde(default)]
    scale_factor: Option<f64>,
}

// ---------------------------------------------------------------------------
// Upstream
// ---------------------------------------------------------------------------

fn join_coords(coords: &[Coordinate]) -> String {
    let mut out = String::with_capacity(coords.len() * 24);
    for (i, c) in coords.iter().enumerate() {
        if i > 0 {
            out.push(';');
        }
        out.push_str(&format!("{},{}", c.longitude, c.latitude));
    }
    out
}

/// sha256 over the sorted params, matching `build_cache_key` in
/// `app/services/cache.py` -- a stable key rather than a salted `hash()`.
fn cache_key(endpoint: &str, params: &[(String, String)]) -> String {
    let mut sorted: Vec<&(String, String)> = params.iter().collect();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    let mut map = Map::new();
    for (k, v) in sorted {
        map.insert(k.clone(), Value::String(v.clone()));
    }
    let serialized = Value::Object(map).to_string();
    let digest = Sha256::digest(serialized.as_bytes());
    format!("{endpoint}:{digest:x}")
}

/// L1 cache-aside around one OSRM GET. The Python gateway consults L2 next;
/// this spike stops here, which the README records as a known asymmetry.
async fn fetch(state: &AppState, endpoint: String, params: Vec<(String, String)>)
    -> Result<Arc<Value>, ApiError>
{
    let key = cache_key(&endpoint, &params);
    if let Some(hit) = state.cache.get(&key).await {
        return Ok(hit);
    }

    let url = format!("{}{}", state.base_url, endpoint);
    let response = state
        .http
        .get(&url)
        .query(&params)
        .send()
        .await
        .map_err(|e| ApiError(StatusCode::BAD_GATEWAY, format!("upstream request failed: {e}")))?;

    let status = response.status();
    let body: Value = response
        .json()
        .await
        .map_err(|e| ApiError(StatusCode::BAD_GATEWAY, format!("upstream returned non-JSON: {e}")))?;

    if !status.is_success() {
        let code = body.get("code").and_then(Value::as_str).unwrap_or("Error");
        let message = body
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Routing service error");
        return Err(ApiError(
            StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::BAD_GATEWAY),
            format!("{code}: {message}"),
        ));
    }

    let value = Arc::new(body);
    state.cache.insert(key, value.clone()).await;
    Ok(value)
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async fn route(State(state): State<AppState>, Json(req): Json<RouteRequest>)
    -> Result<Json<Arc<Value>>, ApiError>
{
    let mut points = vec![req.origin];
    if let Some(waypoints) = &req.waypoints {
        if waypoints.len() > 200 {
            return Err(unprocessable("waypoints must have at most 200 items"));
        }
        points.extend(waypoints.iter().copied());
    }
    points.push(req.destination);
    for point in &points {
        point.validate()?;
    }

    let indices: Vec<String> = (0..points.len()).map(|i| i.to_string()).collect();
    let alternatives = match &req.alternatives {
        Value::Bool(true) => "true".to_string(),
        Value::Bool(false) => "false".to_string(),
        other => other.to_string(),
    };

    let mut params = vec![
        ("overview".to_string(), req.overview),
        ("geometries".to_string(), req.geometries),
        ("steps".to_string(), if req.steps { "true".into() } else { "false".into() }),
        ("waypoints".to_string(), indices.join(";")),
        ("alternatives".to_string(), alternatives),
    ];
    if let Some(annotations) = req.annotations {
        params.push(("annotations".to_string(), annotations));
    }
    if let Some(continue_straight) = req.continue_straight {
        params.push(("continue_straight".to_string(), continue_straight));
    }

    let endpoint = format!("/route/v1/{}/{}", req.profile, join_coords(&points));
    Ok(Json(fetch(&state, endpoint, params).await?))
}

async fn matrix(State(state): State<AppState>, Json(req): Json<MatrixRequest>)
    -> Result<Json<Arc<Value>>, ApiError>
{
    if req.coordinates.len() < 2 || req.coordinates.len() > 5000 {
        return Err(unprocessable("coordinates must have between 2 and 5000 items"));
    }
    for coordinate in &req.coordinates {
        coordinate.validate()?;
    }

    // Same rule osrm-routed applies: sources x destinations against
    // --max-table-size squared, an omitted list meaning every coordinate.
    let total = req.coordinates.len();
    let num_sources = req.sources.as_ref().map_or(total, Vec::len);
    let num_destinations = req.destinations.as_ref().map_or(total, Vec::len);
    let cells = num_sources * num_destinations;
    if cells > state.matrix_max_cells {
        return Err(unprocessable(format!(
            "Matrix of {cells} cells (sources x destinations) exceeds the {}-cell limit; \
             narrow it with 'sources'/'destinations' or split the request",
            state.matrix_max_cells
        )));
    }

    let mut params = vec![("annotations".to_string(), req.annotations)];
    if let Some(sources) = &req.sources {
        if !sources.is_empty() {
            params.push(("sources".to_string(), join_indices(sources)));
        }
    }
    if let Some(destinations) = &req.destinations {
        if !destinations.is_empty() {
            params.push(("destinations".to_string(), join_indices(destinations)));
        }
    }
    if let Some(speed) = req.fallback_speed {
        params.push(("fallback_speed".to_string(), speed.to_string()));
    }
    if let Some(coordinate) = req.fallback_coordinate {
        params.push(("fallback_coordinate".to_string(), coordinate));
    }
    if let Some(factor) = req.scale_factor {
        params.push(("scale_factor".to_string(), factor.to_string()));
    }

    let endpoint = format!("/table/v1/{}/{}", req.profile, join_coords(&req.coordinates));
    Ok(Json(fetch(&state, endpoint, params).await?))
}

fn join_indices(values: &[usize]) -> String {
    values.iter().map(usize::to_string).collect::<Vec<_>>().join(";")
}

/// Probe the engine the way `OSRMClient.ping` does: a minimal route request,
/// short timeout, any non-error status counts as up.
async fn ping(state: &AppState) -> bool {
    let url = format!("{}{}", state.base_url, state.probe_path);
    match state.http.get(&url).timeout(state.probe_timeout).send().await {
        Ok(response) => !response.status().is_server_error() && !response.status().is_client_error(),
        Err(_) => false,
    }
}

async fn health(State(state): State<AppState>) -> Json<Value> {
    let up = ping(&state).await;
    Json(json!({
        "status": if up { "healthy" } else { "degraded" },
        "service": "osrm-gateway-spike",
        "osrm_backend": if up { "up" } else { "down" },
    }))
}

/// `/health` always answers 200 so a dashboard can read the detail, which means
/// a balancer would keep sending traffic to a node that cannot route. This
/// answers 503 in that case so the node drains -- the same split the Python
/// gateway makes, and what both deployment paths probe.
async fn ready(State(state): State<AppState>) -> (StatusCode, Json<Value>) {
    let up = ping(&state).await;
    let body = json!({
        "status": if up { "ready" } else { "not_ready" },
        "service": "osrm-gateway-spike",
        "osrm_backend": if up { "up" } else { "down" },
    });
    let code = if up { StatusCode::OK } else { StatusCode::SERVICE_UNAVAILABLE };
    (code, Json(body))
}

// ---------------------------------------------------------------------------

/// Load `.env` from the working directory the way pydantic-settings does.
///
/// Two rules matter, and a crate got both subtly wrong for this file:
/// **later duplicates win**, because `deploy/freebsd/install.sh` appends a
/// deployment overlay after the shared block and expects it to override
/// (`dotenvy` keeps the *first* occurrence, silently ignoring that overlay);
/// and **a real environment variable beats the file**, so the rc.d script and
/// compose `environment:` still have the last word. Shell sourcing is not an
/// option either -- `HEALTH_CHECK_COORDS=0,0;0,0` would break it.
///
/// A missing file is not an error: the Docker path passes everything through
/// the environment instead.
fn load_dotenv(path: &str) {
    let Ok(contents) = std::fs::read_to_string(path) else {
        return;
    };
    let mut values: HashMap<String, String> = HashMap::new();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        // Insert, not entry(): the last occurrence of a key is the one that counts.
        values.insert(
            key.trim().to_string(),
            value.trim().trim_matches('"').trim_matches('\'').to_string(),
        );
    }
    for (key, value) in values {
        if std::env::var_os(&key).is_none() {
            std::env::set_var(key, value);
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    load_dotenv(&std::env::var("SPIKE_ENV_FILE").unwrap_or_else(|_| ".env".to_string()));

    let settings = Settings::from_env();

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(settings.worker_threads)
        .enable_all()
        .build()?;

    runtime.block_on(async move {
        let state = AppState {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(settings.osrm_client_timeout))
                .build()
                .expect("failed to build HTTP client"),
            cache: Cache::builder()
                .max_capacity(settings.l1_cache_maxsize)
                .time_to_live(Duration::from_secs(settings.l1_cache_ttl))
                .build(),
            base_url: Arc::new(settings.osrm_base_url.clone()),
            matrix_max_cells: settings.matrix_max_cells,
            probe_path: Arc::new(format!("/route/v1/driving/{}", settings.health_check_coords)),
            probe_timeout: Duration::from_secs(settings.health_check_timeout),
        };

        let app = Router::new()
            .route("/route", post(route))
            .route("/matrix", post(matrix))
            .route("/health", axum::routing::get(health))
            .route("/ready", axum::routing::get(ready))
            .with_state(state);

        let bind = format!("{}:{}", settings.host, settings.port);
        let listener = tokio::net::TcpListener::bind(&bind).await?;
        eprintln!(
            "spike listening on {} -> {} ({} worker thread(s))",
            bind, settings.osrm_base_url, settings.worker_threads
        );
        axum::serve(listener, app).await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}
