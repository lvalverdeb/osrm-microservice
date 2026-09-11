//! The upstream `osrm-routed` client: cache-aside reads, bounded retries.
//!
//! Retry policy mirrors the tenacity decorator in `osrm_client.py`, including
//! one consequence that is easy to miss. Only 5xx and transport failures are
//! retried; a 4xx is raised immediately and passed through with its own status.
//! But when retries are *exhausted*, tenacity raises `RetryError`, which is not
//! an `HTTPStatusError`, so it falls to the handler's generic branch and the
//! caller sees **500** -- even if the engine was answering 503 all along. That
//! is reproduced here rather than tidied up: it is observable behaviour, and
//! changing it would make a parity diff ambiguous.

use std::sync::Arc;
use std::time::Duration;

use moka::future::Cache;
use serde_json::Value;

use crate::cache::{build_cache_key, Params};
use crate::metrics::Metrics;
use crate::osrm::params::query_string;
use crate::redis_cache::RedisCache;

/// What went wrong upstream.
#[derive(Debug, Clone)]
pub enum OsrmError {
    /// The engine answered with an error status that is passed through.
    Status { status: u16, body: Option<Value> },
    /// The engine could not be reached, or kept failing until retries ran out.
    /// Surfaces as 500, matching Python's generic handler.
    Unavailable(String),
    /// The request would need a longer URL than the engine will accept.
    ///
    /// OSRM takes coordinates in the path, so a long trace or a wide matrix
    /// builds a request line of tens of kilobytes. Servers cap that around 8 KB
    /// -- a real `osrm-routed` drops the connection, which retries could only
    /// turn into a 500, and a proxy in front answers 414. Caught here instead,
    /// so the caller is told which limit it crossed.
    RequestTooLong { bytes: usize, limit: usize },
    /// MTX-1: the deployment has no engine for this routing profile.
    ///
    /// One `osrm-routed` serves one built graph, so a gateway offering three
    /// profiles needs three engines. Before this existed the profile in the
    /// upstream path was decorative -- OSRM ignores it and answers from
    /// whichever graph it was started with -- so `profile=walking` returned
    /// car routing, byte for byte, with nothing saying so.
    ProfileUnavailable { profile: String, served: Vec<String> },
}

/// How many attempts, and how long to wait between them.
#[derive(Debug, Clone, Copy)]
pub struct RetryPolicy {
    pub attempts: usize,
    pub min_seconds: u64,
    pub max_seconds: u64,
}

impl RetryPolicy {
    /// Delay before attempt `n` (1-based), matching `wait_exponential`.
    pub fn backoff(&self, attempt: usize) -> Duration {
        let raw = 2f64.powi(attempt.saturating_sub(1) as i32);
        // Deliberately not `clamp`, which panics when min > max. tenacity
        // computes `max(min, min(result, max))` and so yields `min` for that
        // misconfiguration; a bad OSRM_RETRY_MIN/MAX pair must not take the
        // request down.
        let clamped = raw.min(self.max_seconds as f64).max(self.min_seconds as f64);
        Duration::from_secs_f64(clamped)
    }
}

/// True when a status is worth another attempt.
///
/// 5xx only: a 4xx means the request itself is wrong, and repeating it just
/// spends the engine's time to get the same answer.
pub fn is_retryable_status(status: u16) -> bool {
    status >= 500
}

/// Which engine serves which routing profile. MTX-1.
///
/// `driving` is required and falls back to `OSRM_BASE_URL`, so a single-engine
/// deployment keeps working. The others are `None` until a deployment stands
/// up a graph for them, and a request for an unserved profile is refused by
/// name rather than answered from the driving graph.
#[derive(Debug, Clone)]
pub struct Upstreams {
    driving: String,
    cycling: Option<String>,
    walking: Option<String>,
}

impl Upstreams {
    pub fn new(base: &str, driving: &str, cycling: &str, walking: &str) -> Self {
        let some = |value: &str| {
            let trimmed = value.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_string())
        };
        Self {
            driving: some(driving).unwrap_or_else(|| base.trim().to_string()),
            cycling: some(cycling),
            walking: some(walking),
        }
    }

    /// The engine for `profile`, or a refusal naming what this deployment has.
    pub fn resolve(&self, profile: &str) -> Result<&str, OsrmError> {
        let found = match profile {
            "cycling" => self.cycling.as_deref(),
            "walking" => self.walking.as_deref(),
            // Anything else, including the tile and health paths, is driving.
            // The `Profile` enum is closed and validated at the edge, so an
            // unrecognised name cannot arrive from a request.
            _ => Some(self.driving.as_str()),
        };
        found.ok_or_else(|| OsrmError::ProfileUnavailable {
            profile: profile.to_string(),
            served: self.served(),
        })
    }

    /// The profiles this deployment actually has a graph for.
    pub fn served(&self) -> Vec<String> {
        let mut names = vec!["driving".to_string()];
        if self.cycling.is_some() {
            names.push("cycling".to_string());
        }
        if self.walking.is_some() {
            names.push("walking".to_string());
        }
        names
    }
}

/// The profile an OSRM path names, if it names one.
///
/// Every upstream path this gateway builds is `/{service}/v1/{profile}/...`.
/// Read from the URL rather than passed alongside it: the request is routed to
/// the engine whose profile the URL actually carries, so the two cannot
/// disagree. Passing it separately would let a handler build one profile's
/// path and send it to another's engine.
fn profile_in(endpoint: &str) -> Option<&str> {
    let mut parts = endpoint.trim_start_matches('/').split('/');
    let _service = parts.next()?;
    (parts.next()? == "v1").then(|| parts.next()).flatten()
}

pub struct OsrmClient {
    http: reqwest::Client,
    cache: Cache<String, Arc<Vec<u8>>>,
    upstreams: Upstreams,
    retry: RetryPolicy,
    /// Ceiling on a constructed upstream URL. See `OsrmError::RequestTooLong`.
    max_url_bytes: usize,
    probe_path: String,
    probe_timeout: Duration,
    metrics: Arc<Metrics>,
    l2: Arc<RedisCache>,
}

impl OsrmClient {
    #[allow(clippy::too_many_arguments, reason = "one call site, all settings-derived")]
    pub fn new(http: reqwest::Client, cache: Cache<String, Arc<Vec<u8>>>, upstreams: Upstreams,
               retry: RetryPolicy, health_check_coords: &str, probe_timeout: Duration,
               metrics: Arc<Metrics>, l2: Arc<RedisCache>, max_url_bytes: usize) -> Self {
        Self {
            http,
            cache,
            upstreams,
            retry,
            max_url_bytes,
            probe_path: format!("/route/v1/driving/{health_check_coords}"),
            probe_timeout,
            metrics,
            l2,
        }
    }

    /// Fetch `endpoint` as raw bytes, serving from the L1 cache when it is warm.
    ///
    /// Deliberately unparsed. For the proxy endpoints the gateway relays the
    /// engine's JSON without computing on it, and a decode/re-encode cycle is
    /// not free of consequence: it shifted about 1 ULP of some geometry
    /// coordinates, because Python's float repr and Rust's shortest-round-trip
    /// formatter do not always choose the same f64 for the same decimal text.
    /// Relaying the bytes makes the body byte-identical to the engine's -- and
    /// therefore to Python's, which round-trips it losslessly -- and skips the
    /// parse entirely on the hot path.
    pub async fn get(&self, endpoint: &str, params: &Params) -> Result<Arc<Vec<u8>>, OsrmError> {
        let key = build_cache_key(endpoint, params);
        if let Some(cached) = self.lookup_cached(&key, endpoint).await {
            return Ok(cached);
        }
        let fetched = Arc::new(self.fetch_with_retry(endpoint, params).await?);
        self.store_cached(&key, Arc::clone(&fetched)).await;
        Ok(fetched)
    }

    /// Consult L1, then L2, recording each lookup.
    ///
    /// The tiers are not independent: L2 is only reached after L1 misses, so its
    /// series are a subset of L1's misses and must not be summed into a single
    /// hit rate. An unconfigured L2 records nothing at all, so a deployment
    /// without Redis reports no L2 series rather than an unbroken run of misses.
    async fn lookup_cached(&self, key: &str, endpoint: &str) -> Option<Arc<Vec<u8>>> {
        if let Some(hit) = self.cache.get(key).await {
            self.metrics.record_lookup("l1", "hit", endpoint);
            return Some(hit);
        }
        self.metrics.record_lookup("l1", "miss", endpoint);

        if !self.l2.is_configured() {
            return None;
        }
        match self.l2.get(key).await {
            Some(bytes) => {
                self.metrics.record_lookup("l2", "hit", endpoint);
                let promoted = Arc::new(bytes);
                // Promote into L1 so the next hit costs nothing.
                self.cache.insert(key.to_string(), Arc::clone(&promoted)).await;
                Some(promoted)
            }
            None => {
                self.metrics.record_lookup("l2", "miss", endpoint);
                None
            }
        }
    }

    /// Write through both tiers.
    async fn store_cached(&self, key: &str, value: Arc<Vec<u8>>) {
        self.cache.insert(key.to_string(), Arc::clone(&value)).await;
        if self.l2.is_configured() {
            self.l2.set(key, &value).await;
        }
    }

    /// Fetch and decode, for the two endpoints that compute on the response.
    pub async fn get_json(&self, endpoint: &str, params: &Params) -> Result<Value, OsrmError> {
        let bytes = self.get(endpoint, params).await?;
        serde_json::from_slice(&bytes)
            .map_err(|e| OsrmError::Unavailable(format!("undecodable upstream body: {e}")))
    }

    /// Fetch a vector tile: no cache, no retry, raw bytes -- as in Python.
    pub async fn get_tile(&self, profile: &str, z: i64, x: i64, y: i64)
        -> Result<Vec<u8>, OsrmError> {
        // Note the reordering: the gateway takes z/x/y and OSRM wants (x,y,z).
        let base = self.upstreams.resolve(profile)?;
        let url = format!("{base}/tile/v1/{profile}/tile({x},{y},{z}).mvt");
        let response = self.send(self.http.get(&url)).await?;
        let status = response.status().as_u16();
        let bytes = response.bytes().await
            .map_err(|e| OsrmError::Unavailable(e.to_string()))?;
        if status >= 400 {
            // Parse the error body as every other endpoint does. Hardcoding
            // `body: None` made `detail` the bare "Routing service error"
            // string for tiles alone, dropping the engine's code and message.
            return Err(OsrmError::Status { status, body: serde_json::from_slice(&bytes).ok() });
        }
        Ok(bytes.to_vec())
    }

    /// Probe the engine. Any non-error status counts as up.
    ///
    /// Bypasses both the cache and the retry policy, so `/health` and `/ready`
    /// answer within their own short timeout rather than inheriting the
    /// request-path budget.
    pub async fn ping(&self) -> bool {
        // The health probe is a driving route; readiness is about the engine
        // this gateway always has, not about optional profiles.
        let url = format!("{}{}", self.upstreams.driving, self.probe_path);
        match self.http.get(&url).timeout(self.probe_timeout).send().await {
            Ok(response) => !response.status().is_server_error() && !response.status().is_client_error(),
            Err(_) => false,
        }
    }

    /// Attempt the upstream call, retrying 5xx and transport failures.
    async fn fetch_with_retry(&self, endpoint: &str, params: &Params) -> Result<Vec<u8>, OsrmError> {
        let base = self.upstreams
            .resolve(profile_in(endpoint).unwrap_or("driving"))?;
        let url = format!("{base}{endpoint}?{}", query_string(params));
        // Checked before the first attempt, not inside the loop: a URL that is
        // too long is too long every time, and retrying it only multiplies the
        // wait before the same failure.
        if url.len() > self.max_url_bytes {
            return Err(OsrmError::RequestTooLong { bytes: url.len(), limit: self.max_url_bytes });
        }
        let mut last = OsrmError::Unavailable("no attempt was made".to_string());
        for attempt in 1..=self.retry.attempts {
            match self.attempt(&url).await {
                Ok(value) => return Ok(value),
                // A 4xx is final: pass it straight through with its own status.
                Err(err @ OsrmError::Status { .. }) if !Self::retryable(&err) => return Err(err),
                Err(err) => last = err,
            }
            if attempt < self.retry.attempts {
                tokio::time::sleep(self.retry.backoff(attempt)).await;
            }
        }
        // Retries exhausted. Python raises RetryError here, which its handlers
        // do not recognise as an upstream status error, so the caller gets 500
        // regardless of what the engine was answering.
        Err(OsrmError::Unavailable(format!("upstream failed after {} attempts: {last:?}",
                                           self.retry.attempts)))
    }

    /// Send one upstream request inside a client span, carrying trace context.
    ///
    /// The span is the counterpart to `HTTPXClientInstrumentor`'s, and the
    /// injected `traceparent` is what lets `osrm-routed` join the caller's
    /// trace instead of starting its own.
    async fn send(&self, request: reqwest::RequestBuilder) -> Result<reqwest::Response, OsrmError> {
        use tracing::Instrument as _;

        let span = tracing::info_span!("http.client", otel.kind = "client");
        async {
            let mut request = request.build()
                .map_err(|e| OsrmError::Unavailable(e.to_string()))?;
            crate::telemetry::inject_context(request.headers_mut());
            self.http.execute(request).await
                .map_err(|e| OsrmError::Unavailable(e.to_string()))
        }.instrument(span).await
    }

    fn retryable(error: &OsrmError) -> bool {
        match error {
            OsrmError::Status { status, .. } => is_retryable_status(*status),
            OsrmError::Unavailable(_) => true,
            // Never: the URL is the same length on every attempt.
            OsrmError::RequestTooLong { .. } => false,
            // Never: no engine is configured for the profile, and retrying
            // cannot configure one.
            OsrmError::ProfileUnavailable { .. } => false,
        }
    }

    async fn attempt(&self, url: &str) -> Result<Vec<u8>, OsrmError> {
        let response = self.send(self.http.get(url)).await?;
        let status = response.status().as_u16();
        let bytes = response.bytes().await
            .map_err(|e| OsrmError::Unavailable(e.to_string()))?;
        if status >= 400 {
            // Only error bodies are parsed, to build the `detail` object.
            return Err(OsrmError::Status { status, body: serde_json::from_slice(&bytes).ok() });
        }
        Ok(bytes.to_vec())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const POLICY: RetryPolicy = RetryPolicy { attempts: 3, min_seconds: 1, max_seconds: 10 };

    /// A URL that cannot fit is not worth three attempts and a backoff.
    #[test]
    fn an_oversized_url_is_never_retried() {
        assert!(!OsrmClient::retryable(&OsrmError::RequestTooLong { bytes: 40_000, limit: 8_000 }));
        // The two that are retried keep their existing classification.
        assert!(OsrmClient::retryable(&OsrmError::Unavailable("reset".to_string())));
    }

    #[test]
    fn only_server_errors_are_retried() {
        assert!(is_retryable_status(500));
        assert!(is_retryable_status(503));
        // A 4xx repeated is the same 4xx; Python does not retry it either.
        assert!(!is_retryable_status(400));
        assert!(!is_retryable_status(404));
        assert!(!is_retryable_status(429));
    }

    #[test]
    fn transport_failures_are_always_retryable() {
        assert!(OsrmClient::retryable(&OsrmError::Unavailable("connection refused".into())));
    }

    #[test]
    fn client_errors_are_not_retryable() {
        assert!(!OsrmClient::retryable(&OsrmError::Status { status: 400, body: None }));
    }

    #[test]
    fn backoff_grows_and_is_clamped() {
        assert_eq!(POLICY.backoff(1), Duration::from_secs(1));
        assert_eq!(POLICY.backoff(2), Duration::from_secs(2));
        assert_eq!(POLICY.backoff(3), Duration::from_secs(4));
        // Clamped at max rather than doubling forever.
        assert_eq!(POLICY.backoff(9), Duration::from_secs(10));
    }

    #[test]
    fn backoff_respects_the_minimum() {
        let slow = RetryPolicy { attempts: 3, min_seconds: 5, max_seconds: 10 };
        assert_eq!(slow.backoff(1), Duration::from_secs(5));
    }

    // ----------------------------------------------------------------------
    // MTX-1: one engine per routing profile
    // ----------------------------------------------------------------------

    fn three() -> Upstreams {
        Upstreams::new("http://base:5000", "", "http://bike:5001", "http://foot:5002")
    }

    #[test]
    fn driving_falls_back_to_the_base_url() {
        // A single-engine deployment keeps working without new settings.
        assert_eq!(three().resolve("driving").unwrap(), "http://base:5000");
    }

    #[test]
    fn each_profile_reaches_its_own_engine() {
        let up = three();
        assert_eq!(up.resolve("cycling").unwrap(), "http://bike:5001");
        assert_eq!(up.resolve("walking").unwrap(), "http://foot:5002");
    }

    #[test]
    fn an_unserved_profile_is_refused_rather_than_answered_by_driving() {
        // The defect this exists to remove: `profile=walking` used to return
        // car routing byte for byte, because OSRM ignores the profile in the
        // path and answers from whichever graph it was started with.
        let only_driving = Upstreams::new("http://base:5000", "", "", "");
        match only_driving.resolve("walking") {
            Err(OsrmError::ProfileUnavailable { profile, served }) => {
                assert_eq!(profile, "walking");
                assert_eq!(served, vec!["driving".to_string()]);
            }
            other => panic!("expected a refusal, got {other:?}"),
        }
    }

    #[test]
    fn served_lists_only_profiles_with_a_graph() {
        assert_eq!(Upstreams::new("b", "", "", "").served(), vec!["driving"]);
        assert_eq!(three().served(), vec!["driving", "cycling", "walking"]);
    }

    #[test]
    fn the_profile_comes_from_the_path_that_is_actually_sent() {
        // Read from the URL rather than passed beside it, so a handler cannot
        // build one profile's path and send it to another's engine.
        assert_eq!(profile_in("/route/v1/cycling/1,2;3,4"), Some("cycling"));
        assert_eq!(profile_in("/table/v1/walking/1,2"), Some("walking"));
        assert_eq!(profile_in("/nearest/v1/driving/1,2"), Some("driving"));
    }

    #[test]
    fn a_path_that_names_no_profile_is_treated_as_driving() {
        assert_eq!(profile_in("/health"), None);
        assert_eq!(profile_in("/route/v2/cycling/1,2"), None);
    }

    #[test]
    fn an_unserved_profile_is_never_retried() {
        // Retrying cannot configure an engine.
        assert!(!OsrmClient::retryable(&OsrmError::ProfileUnavailable {
            profile: "walking".into(), served: vec!["driving".into()] }));
    }
}
