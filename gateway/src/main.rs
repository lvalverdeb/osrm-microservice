//! OSRM API Gateway.
//!
//! A port of the FastAPI gateway in `src/app`, endpoint for endpoint. See
//! `docs/planning/SCALING_READINESS_PLAN.md` for why it exists and what the
//! measurements behind it do and do not show.

mod admission;
mod cache;
mod config;
mod error;
mod handlers;
mod metrics;
mod models;
mod openapi;
mod osrm;
mod pyfloat;
mod ratelimit;
mod redis_cache;
mod telemetry;
mod version;
mod vrp;

use std::sync::Arc;
use std::time::Duration;

use std::net::SocketAddr;

use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderValue, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use moka::future::Cache;

use crate::config::Settings;
use crate::handlers::AppState;
use crate::osrm::client::{OsrmClient, RetryPolicy};
use crate::metrics::Metrics;
use crate::ratelimit::{client_key, Limits, RateLimiter, TrustedProxies};
use crate::redis_cache::RedisCache;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    config::load_dotenv(".env");
    let settings = Settings::from_env();

    // WORKERS becomes tokio worker threads sharing one process, rather than the
    // separate uvicorn processes the Python path spawns. That is what removes
    // the PROMETHEUS_MULTIPROC_DIR machinery: one registry, no aggregation, no
    // directory to wipe on start.
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(settings.workers.max(1))
        .enable_all()
        .build()?;

    runtime.block_on(serve(settings))
}

async fn serve(settings: Settings) -> Result<(), Box<dyn std::error::Error>> {
    // Held for the process lifetime so the final span batch is flushed.
    let _telemetry = telemetry::setup(&settings);
    let http = reqwest::Client::builder()
        .timeout(Duration::from_secs(settings.osrm_client_timeout))
        // httpx defaults to following none, and reqwest to following ten. A
        // redirect from the engine is a misconfiguration, not a route to
        // chase: following it silently turned a 3xx into someone else's body.
        .redirect(reqwest::redirect::Policy::none())
        .build()?;
    let cache = Cache::builder()
        .max_capacity(settings.l1_cache_maxsize)
        .time_to_live(Duration::from_secs(settings.l1_cache_ttl))
        .build();
    let retry = RetryPolicy {
        attempts: settings.osrm_retry_attempts.max(1),
        min_seconds: settings.osrm_retry_min,
        max_seconds: settings.osrm_retry_max,
    };
    let metrics = Arc::new(Metrics::new());
    let l2 = Arc::new(RedisCache::new(&settings.redis_url, settings.redis_ttl));
    let client = OsrmClient::new(http, cache, settings.osrm_base_url.clone(), retry,
                                 &settings.health_check_coords,
                                 Duration::from_secs(settings.health_check_timeout),
                                 Arc::clone(&metrics), Arc::clone(&l2),
                                 settings.max_url_bytes);

    let l2_for_shutdown = Arc::clone(&l2);
    let bind = format!("{}:{}", settings.host, settings.port);
    let metrics_path = settings.metrics_endpoint.clone();
    let vrp_gate = Arc::new(admission::AdmissionGate::new(
        settings.vrp_max_concurrency,
        settings.vrp_queue_timeout,
        settings.vrp_max_queue_depth,
    ));
    let limits = Limits::from_settings(&settings)?;
    let trusted_proxies = Arc::new(TrustedProxies::parse(&settings.forwarded_allow_ips));
    let state = AppState {
        client: Arc::new(client),
        settings: Arc::new(settings),
        vrp_gate,
        limiter: Arc::new(RateLimiter::new()),
        l2,
        limits,
        trusted_proxies,
        metrics,
    };
    tracing::info!(%bind, upstream = %state.settings.osrm_base_url,
                   workers = state.settings.workers, "osrm-api-gateway starting");
    let app = router(state, &metrics_path);

    let listener = tokio::net::TcpListener::bind(&bind).await?;
    tracing::info!(%bind, "osrm-api-gateway listening");
    // into_make_service_with_connect_info is what gives the limiter the peer
    // address; without it there is nothing to key an unproxied client on.
    axum::serve(listener, app.into_make_service_with_connect_info::<SocketAddr>())
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    // The lifespan closed both of these after the server stopped; the reqwest
    // client needs no counterpart, since dropping it closes its pool.
    l2_for_shutdown.close().await;
    Ok(())
}

/// The endpoints `NFR-10` versions: everything a client integrates against.
///
/// `/health`, `/ready` and the metrics scrape are deliberately not here. They
/// are an operator's contract with its own orchestrator, not a public API, and
/// versioning them would break every liveness probe and Prometheus job for a
/// promise nobody asked for. The docs endpoints stay unversioned for the same
/// reason a directory is not one of the things it lists.
fn data_plane() -> Router<AppState> {
    Router::new()
        .route("/route", post(handlers::route))
        .route("/matrix", post(handlers::matrix))
        .route("/matrix-graph", post(handlers::matrix_graph))
        .route("/match", post(handlers::match_trace))
        .route("/trip", post(handlers::trip))
        .route("/nearest", post(handlers::nearest))
        .route("/vrp", post(handlers::vrp))
        .route("/vrp/allocate", post(handlers::vrp_allocate))
        // The final segment arrives as `{y}.mvt`; the handler strips the suffix.
        .route("/tile/{profile}/{z}/{x}/{y}", get(handlers::tile))
}

fn router(state: AppState, metrics_path: &str) -> Router {
    Router::new()
        // NFR-10, §9.4: the versioned surface, which is the one to integrate
        // against. Served from the same `data_plane()` as the deprecated root
        // paths rather than a copy, so the two cannot drift.
        .nest(version::PREFIX, data_plane())
        .merge(data_plane())
        .route("/health", get(handlers::health))
        .route("/ready", get(handlers::ready))
        .route(metrics_path, get(handlers::metrics))
        .route("/openapi.json", get(handlers::openapi))
        .route("/docs", get(handlers::docs))
        .route("/redoc", get(handlers::redoc))
        .route("/docs/oauth2-redirect", get(handlers::oauth2_redirect))
        // Starlette answered these as JSON `{"detail": ...}`; axum's default
        // fallback sends an empty body with no content-type, so a client
        // parsing `detail` got nothing to parse.
        .fallback(handlers::not_found)
        .method_not_allowed_fallback(handlers::method_not_allowed)
        // Outermost of the response-shaping layers, so it sees the finished
        // response whatever produced it -- including a 404 from the fallback,
        // which is still a request on the deprecated spelling and still worth
        // telling a client about.
        .layer(middleware::from_fn_with_state(state.clone(), announce_deprecation))
        .layer(middleware::from_fn(require_json_body))
        .layer(middleware::from_fn_with_state(state.clone(), rate_limit))
        .layer(middleware::from_fn_with_state(state.clone(), observe))
        // Innermost of the three, so `observe` still counts a panicking request
        // and reports it as the 500 it became.
        .layer(tower_http::catch_panic::CatchPanicLayer::custom(panic_response))
        .with_state(state)
}

/// Tell a client on the deprecated unversioned surface where its endpoint went.
///
/// NFR-10 asks for "a deprecation window", and a window that nothing announces
/// is one every integrator discovers the day it closes. `Deprecation: true` and
/// a `Link` to the successor are sent on every unversioned response; `Sunset`
/// only when `API_SUNSET` names a date, because committing an operator to one
/// they did not choose is worse than sending no date at all.
///
/// The versioned surface carries none of these: a client that migrated must not
/// keep being told to migrate.
async fn announce_deprecation(State(state): State<AppState>,
                              request: axum::extract::Request,
                              next: Next) -> Response {
    let path = request.uri().path().to_string();
    let mut response = next.run(request).await;
    if !version::is_unversioned(&path) || !version::has_versioned_successor(&path) {
        return response;
    }
    let headers = response.headers_mut();
    // RFC 9745 for the flag, RFC 8288 for the pointer, RFC 8594 for the date.
    // Header values are built from a path this gateway routed and a setting an
    // operator wrote; a malformed one is dropped rather than made fatal, since
    // a working response with no advisory beats a 500 with one.
    headers.insert("deprecation", HeaderValue::from_static("true"));
    if let Ok(value) = HeaderValue::from_str(
        &format!("<{}{}>; rel=\"successor-version\"", version::PREFIX, path)) {
        headers.insert("link", value);
    }
    if !state.settings.api_sunset.is_empty() {
        if let Ok(value) = HeaderValue::from_str(&state.settings.api_sunset) {
            headers.insert("sunset", value);
        }
    }
    response
}

/// Count every request and time it.
///
/// Outside the rate-limit layer, so a shed 429 is still counted -- otherwise
/// the metric would go quiet exactly when the gateway is under pressure.
async fn observe(State(state): State<AppState>, request: axum::extract::Request,
                 next: Next) -> Response {
    let handler = crate::metrics::handler_label(request.uri().path(),
                                               &state.settings.metrics_endpoint);
    let method = request.method().to_string();
    // Content-Length is what the Python instrumentator measures too, so an
    // unlabelled or chunked body counts as zero on both sides.
    let request_bytes = content_length(request.headers());
    let timer = state.metrics.duration
        .with_label_values(&[&handler, &method])
        .start_timer();
    let highr = state.metrics.duration_highr.start_timer();

    // The span the collector sees. Nothing in the port created one, so an
    // OTLP endpoint could be configured and correctly connected and still
    // receive an empty trace -- the layer was installed over no spans at all.
    // status_code is declared Empty and filled after the response: `record` on
    // an undeclared field is silently dropped.
    let span = tracing::info_span!("http.server", otel.name = %format!("{method} {handler}"),
                                   http.request.method = %method, http.route = %handler,
                                   http.response.status_code = tracing::field::Empty);
    // Parent this span on the caller's, when they sent one. Injecting
    // downstream without extracting here left the gateway starting a fresh
    // trace on every request, so a caller's trace ended at this hop --
    // `FastAPIInstrumentor` extracted, and only fixing the outbound half of
    // propagation looks like it works right up until someone traces end to end.
    crate::telemetry::adopt_incoming_context(&span, request.headers());
    let response = {
        use tracing::Instrument as _;
        next.run(request).instrument(span.clone()).await
    };
    span.record("http.response.status_code", response.status().as_u16());
    timer.observe_duration();
    highr.observe_duration();

    // Reading `Content-Length` off the response records zero for every request:
    // axum does not set it, hyper adds it while encoding the wire, long after
    // this layer has run. Starlette set it before its middleware saw the
    // response, which is why the same code works on the Python side. Measure
    // the body instead. Every response this gateway produces is already fully
    // buffered -- relayed engine bytes, a serialised struct, a tile -- so this
    // collects an in-memory body rather than draining a stream.
    let (parts, body) = response.into_parts();
    let bytes = axum::body::to_bytes(body, usize::MAX).await.unwrap_or_default();
    state.metrics.request_size.observe(&handler, request_bytes);
    state.metrics.response_size.observe(&handler, bytes.len() as f64);
    state.metrics.requests
        .with_label_values(&[&handler, &method,
                             crate::metrics::status_label(parts.status.as_u16())])
        .inc();
    Response::from_parts(parts, axum::body::Body::from(bytes))
}

/// Refuse a request body that is not declared as JSON.
///
/// FastAPI parsed a body as JSON only when the media type said so, and answered
/// 422 otherwise; `parse_body` deserialises unconditionally, so a `text/plain`
/// body carrying JSON was accepted here and rejected there. Every POST this
/// gateway routes takes a JSON body -- there is no form or upload endpoint --
/// so the method alone identifies the requests this applies to.
async fn require_json_body(request: axum::extract::Request, next: Next) -> Response {
    if request.method() != axum::http::Method::POST {
        return next.run(request).await;
    }
    let declared = request.headers().get(axum::http::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(|value| value.split(';').next().unwrap_or("").trim().to_ascii_lowercase());
    let acceptable = match declared.as_deref() {
        // FastAPI accepts `application/json` and the `+json` structured suffix.
        Some(media) => media == "application/json" || media.ends_with("+json"),
        // An absent Content-Type is what a bare `curl -d` sends; FastAPI treats
        // it as a form body and fails the model, so it is refused here too.
        None => false,
    };
    if acceptable {
        return next.run(request).await;
    }
    crate::error::ApiError::Validation(vec![crate::models::ValidationError::path_param(
        "model_attributes_type", "body",
        "Input should be a valid dictionary or object to extract fields from",
        declared.as_deref().unwrap_or(""))])
        .into_response()
}

/// Turn a panic into the 500 Starlette's `ServerErrorMiddleware` produced.
fn panic_response(panic: Box<dyn std::any::Any + Send + 'static>) -> Response {
    let detail = panic.downcast_ref::<&str>().map(|s| s.to_string())
        .or_else(|| panic.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "unknown panic".to_string());
    tracing::error!(%detail, "panic while handling a request");
    // The body matches every other unexpected failure; the cause goes to the
    // log rather than to the caller.
    (StatusCode::INTERNAL_SERVER_ERROR,
     Json(serde_json::json!({ "detail": "Internal server error" }))).into_response()
}

/// Body size from `Content-Length`, or zero when it is absent or unparseable.
fn content_length(headers: &axum::http::HeaderMap) -> f64 {
    headers.get(axum::http::header::CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<f64>().ok())
        .unwrap_or(0.0)
}

/// Reject requests past their endpoint's allowance.
///
/// Applied as one layer rather than per handler, but only to the paths the
/// Python gateway decorates -- `/health`, `/ready` and `/metrics` stay
/// unlimited so probes and scrapes are never shed.
async fn rate_limit(State(state): State<AppState>, ConnectInfo(peer): ConnectInfo<SocketAddr>,
                    request: axum::extract::Request, next: Next) -> Response {
    let Some((label, limit)) = state.limits.for_path(request.uri().path()) else {
        return next.run(request).await;
    };
    let forwarded = request.headers().get("x-forwarded-for").and_then(|v| v.to_str().ok());
    let client = client_key(&peer.ip().to_string(), forwarded, &state.trusted_proxies);

    if state.limiter.check_shared(Some(&state.l2), &client, label, limit).await {
        return next.run(request).await;
    }
    // slowapi's own wording and shape, which clients may be matching on. Note
    // it sends no Retry-After, so neither does this.
    (StatusCode::TOO_MANY_REQUESTS,
     Json(serde_json::json!({ "error": format!("Rate limit exceeded: {}", limit.describe()) })))
        .into_response()
}

/// Exit cleanly on SIGTERM.
///
/// Both deployments need this: Docker sends SIGTERM to PID 1, and on FreeBSD
/// `daemon(8)` reaps the child. Neither expects the process to restart itself.
async fn shutdown_signal() {
    use tokio::signal::unix::{signal, SignalKind};
    let mut terminate = match signal(SignalKind::terminate()) {
        Ok(stream) => stream,
        Err(_) => return,
    };
    tokio::select! {
        _ = terminate.recv() => {}
        _ = tokio::signal::ctrl_c() => {}
    }
}
