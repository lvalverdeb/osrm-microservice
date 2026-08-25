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
        let clamped = raw.clamp(self.min_seconds as f64, self.max_seconds as f64);
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

pub struct OsrmClient {
    http: reqwest::Client,
    cache: Cache<String, Arc<Vec<u8>>>,
    base_url: String,
    retry: RetryPolicy,
    probe_path: String,
    probe_timeout: Duration,
    metrics: Arc<Metrics>,
    l2: Arc<RedisCache>,
}

impl OsrmClient {
    pub fn new(http: reqwest::Client, cache: Cache<String, Arc<Vec<u8>>>, base_url: String,
               retry: RetryPolicy, health_check_coords: &str, probe_timeout: Duration,
               metrics: Arc<Metrics>, l2: Arc<RedisCache>) -> Self {
        Self {
            http,
            cache,
            base_url,
            retry,
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
        let url = format!("{}/tile/v1/{profile}/tile({x},{y},{z}).mvt", self.base_url);
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
        let url = format!("{}{}", self.base_url, self.probe_path);
        match self.http.get(&url).timeout(self.probe_timeout).send().await {
            Ok(response) => !response.status().is_server_error() && !response.status().is_client_error(),
            Err(_) => false,
        }
    }

    /// Attempt the upstream call, retrying 5xx and transport failures.
    async fn fetch_with_retry(&self, endpoint: &str, params: &Params) -> Result<Vec<u8>, OsrmError> {
        let url = format!("{}{}?{}", self.base_url, endpoint, query_string(params));
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
}
