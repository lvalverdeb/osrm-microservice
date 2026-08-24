//! Prometheus metrics.
//!
//! One registry for the whole process. The Python path needs
//! `PROMETHEUS_MULTIPROC_DIR` and a collector that aggregates across uvicorn
//! workers, plus a directory wiped on every start; here `WORKERS` is tokio
//! threads inside one process, so the multiprocess machinery has nothing to do
//! and is simply absent -- along with the entrypoint script and rc.d `precmd`
//! step that maintained it.

use prometheus::{Encoder, HistogramVec, IntCounterVec, Registry, TextEncoder};

/// OSRM services that get their own `service` label value.
///
/// Anything else collapses to `other`, because the raw endpoint carries the
/// request's coordinates and would give the label unbounded cardinality.
const KNOWN_SERVICES: [&str; 5] = ["route", "table", "match", "trip", "nearest"];

pub struct Metrics {
    registry: Registry,
    pub cache_lookups: IntCounterVec,
    pub requests: IntCounterVec,
    pub duration: HistogramVec,
}

impl Metrics {
    pub fn new() -> Self {
        let registry = Registry::new();
        let cache_lookups = IntCounterVec::new(
            prometheus::opts!("cache_lookups_total", "Cache lookups by tier and result"),
            &["tier", "result", "service"],
        ).expect("metric definition is valid");
        let requests = IntCounterVec::new(
            prometheus::opts!("http_requests_total", "HTTP requests by handler and status"),
            &["handler", "method", "status"],
        ).expect("metric definition is valid");
        let duration = HistogramVec::new(
            prometheus::histogram_opts!("http_request_duration_seconds",
                                        "HTTP request latency in seconds"),
            &["handler", "method"],
        ).expect("metric definition is valid");

        registry.register(Box::new(cache_lookups.clone())).expect("fresh registry");
        registry.register(Box::new(requests.clone())).expect("fresh registry");
        registry.register(Box::new(duration.clone())).expect("fresh registry");
        Self { registry, cache_lookups, requests, duration }
    }

    /// Count one cache lookup.
    ///
    /// Note the tiers are not independent: L2 is consulted only after L1 misses,
    /// so its series are a subset of L1's misses and summing them for a hit rate
    /// double-counts.
    pub fn record_lookup(&self, tier: &str, result: &str, endpoint: &str) {
        self.cache_lookups.with_label_values(&[tier, result, service_label(endpoint)]).inc();
    }

    /// Render the registry in Prometheus text format.
    pub fn encode(&self) -> String {
        let mut buffer = Vec::new();
        let encoder = TextEncoder::new();
        if encoder.encode(&self.registry.gather(), &mut buffer).is_err() {
            return String::new();
        }
        String::from_utf8(buffer).unwrap_or_default()
    }
}

impl Default for Metrics {
    fn default() -> Self {
        Self::new()
    }
}

/// Reduce an upstream endpoint to a bounded label.
pub fn service_label(endpoint: &str) -> &'static str {
    let segment = endpoint.trim_start_matches('/').split('/').next().unwrap_or("");
    KNOWN_SERVICES.iter().copied().find(|known| *known == segment).unwrap_or("other")
}

/// Reduce a request path to a bounded handler label.
///
/// `/tile` carries coordinates in its path, so it collapses to its route
/// pattern; anything unrouted collapses to `other` for the same reason.
pub fn handler_label(path: &str) -> String {
    match path {
        "/route" | "/matrix" | "/matrix-graph" | "/match" | "/trip" | "/nearest"
        | "/vrp" | "/vrp/allocate" | "/health" | "/ready" | "/metrics" => path.to_string(),
        _ if path.starts_with("/tile/") => "/tile/{profile}/{z}/{x}/{y}".to_string(),
        _ => "other".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_services_keep_their_name() {
        assert_eq!(service_label("/route/v1/driving/0,0;1,1"), "route");
        assert_eq!(service_label("/table/v1/driving/0,0"), "table");
        assert_eq!(service_label("/nearest/v1/driving/0,0"), "nearest");
    }

    /// The cardinality guard: a coordinate-bearing endpoint must not become a
    /// label value of its own.
    #[test]
    fn unknown_services_collapse_to_other() {
        assert_eq!(service_label("/tile/v1/driving/tile(1,2,3).mvt"), "other");
        assert_eq!(service_label("/something/else"), "other");
        assert_eq!(service_label(""), "other");
    }

    #[test]
    fn tile_paths_collapse_to_their_route_pattern() {
        assert_eq!(handler_label("/tile/driving/12/100/200.mvt"),
                   "/tile/{profile}/{z}/{x}/{y}");
        assert_eq!(handler_label("/tile/driving/12/101/201.mvt"),
                   "/tile/{profile}/{z}/{x}/{y}");
    }

    #[test]
    fn routed_paths_keep_their_own_label() {
        assert_eq!(handler_label("/vrp/allocate"), "/vrp/allocate");
        assert_eq!(handler_label("/unrouted"), "other");
    }

    #[test]
    fn lookups_are_counted_by_tier_and_result() {
        let metrics = Metrics::new();
        metrics.record_lookup("l1", "hit", "/route/v1/driving/0,0");
        metrics.record_lookup("l1", "miss", "/route/v1/driving/0,0");
        metrics.record_lookup("l1", "hit", "/route/v1/driving/0,0");
        let text = metrics.encode();
        assert!(text.contains(r#"cache_lookups_total{result="hit",service="route",tier="l1"} 2"#),
                "{text}");
        assert!(text.contains(r#"cache_lookups_total{result="miss",service="route",tier="l1"} 1"#),
                "{text}");
    }

    #[test]
    fn the_exposition_carries_help_and_type_lines() {
        let metrics = Metrics::new();
        metrics.record_lookup("l1", "hit", "/route/x");
        let text = metrics.encode();
        assert!(text.contains("# HELP cache_lookups_total"));
        assert!(text.contains("# TYPE cache_lookups_total counter"));
    }
}
