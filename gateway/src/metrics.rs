//! Prometheus metrics.
//!
//! One registry for the whole process. The Python path needs
//! `PROMETHEUS_MULTIPROC_DIR` and a collector that aggregates across uvicorn
//! workers, plus a directory wiped on every start; here `WORKERS` is tokio
//! threads inside one process, so the multiprocess machinery has nothing to do
//! and is simply absent -- along with the entrypoint script and rc.d `precmd`
//! step that maintained it.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use prometheus::core::{Collector, Desc};
use prometheus::{proto, Encoder, Histogram, HistogramVec, IntCounterVec, Registry, TextEncoder};

/// Latency buckets, matching `prometheus-fastapi-instrumentator`'s
/// `latency_lowr_buckets` default. The prometheus crate's `DEFAULT_BUCKETS`
/// span 0.005..10 in twelve steps, so leaving them unset gave the same metric
/// name a different bucket set and made `histogram_quantile` disagree with the
/// Python deployment over identical traffic.
const LATENCY_BUCKETS: [f64; 3] = [0.1, 0.5, 1.0];

/// The instrumentator's `latency_highr_buckets` default, all 21 boundaries.
///
/// Truncating this at 7.5 put every slow request in `+Inf`, so tail quantiles
/// disagreed with the Python deployment over identical traffic -- the exact
/// failure the bucket alignment was meant to prevent.
const HIGHR_BUCKETS: [f64; 21] = [0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5,
                                  2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 7.5, 10.0, 30.0, 60.0];

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
    /// Unlabelled, high-resolution companion to `duration`, as Python exposes.
    pub duration_highr: Histogram,
    pub request_size: SizeSummary,
    pub response_size: SizeSummary,
}

/// A `handler`-labelled summary carrying only `_sum` and `_count`.
///
/// `prometheus_client`'s `Summary` -- what the instrumentator uses for the two
/// size metrics -- exposes exactly those two series and no quantiles. The
/// prometheus crate has no summary type at all, so rather than substitute a
/// histogram (same `_sum`/`_count`, but a `histogram` TYPE line and a spray of
/// `_bucket` series Python never emitted) this collects the pair directly.
#[derive(Clone)]
pub struct SizeSummary {
    desc: Arc<Desc>,
    name: String,
    help: String,
    /// handler -> (count, sum). Shared, so a registered clone and the caller's
    /// handle observe the same state -- the shape every built-in metric uses.
    observations: Arc<Mutex<HashMap<String, (u64, f64)>>>,
}

impl SizeSummary {
    fn new(name: &str, help: &str) -> Self {
        let desc = Desc::new(name.to_string(), help.to_string(),
                             vec!["handler".to_string()], HashMap::new())
            .expect("metric definition is valid");
        Self { desc: Arc::new(desc), name: name.to_string(), help: help.to_string(),
               observations: Arc::new(Mutex::new(HashMap::new())) }
    }

    /// Record one body size against a handler.
    pub fn observe(&self, handler: &str, bytes: f64) {
        // A poisoned lock costs an observation rather than the request: this is
        // instrumentation, and panicking here would take down a served response.
        let Ok(mut observations) = self.observations.lock() else {
            return;
        };
        let entry = observations.entry(handler.to_string()).or_insert((0, 0.0));
        entry.0 += 1;
        entry.1 += bytes;
    }
}

impl Collector for SizeSummary {
    fn desc(&self) -> Vec<&Desc> {
        vec![&self.desc]
    }

    fn collect(&self) -> Vec<proto::MetricFamily> {
        let Ok(observations) = self.observations.lock() else {
            return Vec::new();
        };
        let metrics = observations.iter().map(|(handler, (count, sum))| {
            let mut label = proto::LabelPair::default();
            label.set_name("handler".to_string());
            label.set_value(handler.clone());
            let mut summary = proto::Summary::default();
            summary.set_sample_count(*count);
            summary.set_sample_sum(*sum);
            let mut metric = proto::Metric::default();
            metric.set_label(vec![label]);
            metric.set_summary(summary);
            metric
        }).collect();

        let mut family = proto::MetricFamily::default();
        family.set_name(self.name.clone());
        family.set_help(self.help.clone());
        family.set_field_type(proto::MetricType::SUMMARY);
        family.set_metric(metrics);
        vec![family]
    }
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
                                        "HTTP request latency in seconds",
                                        LATENCY_BUCKETS.to_vec()),
            &["handler", "method"],
        ).expect("metric definition is valid");
        let duration_highr = Histogram::with_opts(
            prometheus::histogram_opts!("http_request_duration_highr_seconds",
                                        "HTTP request latency, high resolution, all handlers",
                                        HIGHR_BUCKETS.to_vec()),
        ).expect("metric definition is valid");
        let request_size = SizeSummary::new("http_request_size_bytes", "Request body size");
        let response_size = SizeSummary::new("http_response_size_bytes", "Response body size");
        registry.register(Box::new(request_size.clone())).expect("fresh registry");
        registry.register(Box::new(response_size.clone())).expect("fresh registry");

        registry.register(Box::new(cache_lookups.clone())).expect("fresh registry");
        registry.register(Box::new(requests.clone())).expect("fresh registry");
        registry.register(Box::new(duration.clone())).expect("fresh registry");
        registry.register(Box::new(duration_highr.clone())).expect("fresh registry");
        // The process_* collectors prometheus_client registered by default:
        // process_cpu_seconds_total, process_resident_memory_bytes,
        // process_open_fds and process_start_time_seconds. Reading them needs
        // /proc, so this is Linux-only -- the Docker deployment and CI get
        // them, the FreeBSD jail does not, and nothing else changes there.
        #[cfg(target_os = "linux")]
        registry.register(Box::new(prometheus::process_collector::ProcessCollector::for_self()))
            .expect("fresh registry");

        Self { registry, cache_lookups, requests, duration, duration_highr,
               request_size, response_size }
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

/// Group a status code the way `should_group_status_codes` does.
///
/// The instrumentator's default buckets these as `2xx`/`4xx`/`5xx`, so emitting
/// the full code under the same metric and label name left every existing
/// `http_requests_total{status="5xx"}` query matching nothing.
pub fn status_label(status: u16) -> &'static str {
    match status / 100 {
        1 => "1xx",
        2 => "2xx",
        3 => "3xx",
        4 => "4xx",
        _ => "5xx",
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
pub fn handler_label(path: &str, metrics_endpoint: &str) -> String {
    // The scrape path is configurable, and hardcoding `/metrics` counted every
    // scrape under the unrouted bucket whenever METRICS_ENDPOINT was moved.
    if path == metrics_endpoint {
        return path.to_string();
    }
    match path {
        "/route" | "/matrix" | "/matrix-graph" | "/match" | "/trip" | "/nearest"
        | "/vrp" | "/vrp/allocate" | "/health" | "/ready" | "/metrics"
        // Labelled by path in Python too; collapsing them into `other` mixed
        // doc traffic in with genuinely unrouted requests.
        | "/docs" | "/redoc" | "/openapi.json" => path.to_string(),
        // The route template FastAPI registered carried the suffix, and the
        // label is the join key for any dashboard spanning the two.
        _ if path.starts_with("/tile/") => "/tile/{profile}/{z}/{x}/{y}.mvt".to_string(),
        // `should_group_untemplated` is on by default in the instrumentator,
        // and its bucket is spelled `none`; `other` matched no existing query.
        _ => "none".to_string(),
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
        assert_eq!(handler_label("/tile/driving/12/100/200.mvt", "/metrics"),
                   "/tile/{profile}/{z}/{x}/{y}.mvt");
        assert_eq!(handler_label("/tile/driving/12/101/201.mvt", "/metrics"),
                   "/tile/{profile}/{z}/{x}/{y}.mvt");
    }

    /// The scrape path is configurable; the label must follow it.
    #[test]
    fn a_relocated_metrics_endpoint_keeps_its_own_label() {
        assert_eq!(handler_label("/internal/metrics", "/internal/metrics"), "/internal/metrics");
        // And the default spelling is no longer special when it is not the one
        // configured -- it is just another unrouted path.
        assert_eq!(handler_label("/internal/metrics", "/metrics"), "none");
    }

    #[test]
    fn routed_paths_keep_their_own_label() {
        assert_eq!(handler_label("/vrp/allocate", "/metrics"), "/vrp/allocate");
        assert_eq!(handler_label("/docs", "/metrics"), "/docs");
        assert_eq!(handler_label("/openapi.json", "/metrics"), "/openapi.json");
        assert_eq!(handler_label("/unrouted", "/metrics"), "none");
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
