//! The shared L2 cache tier.
//!
//! Degrades rather than fails: every Redis error is logged and swallowed, so
//! losing Redis costs cache hits and nothing else. That posture is load-bearing
//! here -- the jail deployment ran for a while with Redis unreachable and served
//! traffic throughout, which is how the problem stayed hidden long enough to be
//! worth a note in `docs/deployment_freebsd.md`.
//!
//! **Payload format caveat.** This tier stores the engine's response bytes
//! verbatim, while the Python gateway stores `json.dumps` of the decoded body --
//! same JSON, different whitespace. Keys match byte for byte, so the two share
//! entries; a value written by Python and read here is therefore relayed with
//! Python's spacing rather than the engine's. Cosmetic for any JSON client, and
//! only reachable while both gateways run against one Redis.

use std::time::Duration;

use redis::AsyncCommands;

/// Ceiling on a single connect or command against L2.
///
/// L2 is an optimisation: waiting longer than this for it costs more than the
/// cache hit is worth, and the caller has a live upstream to fall back to.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);

pub struct RedisCache {
    client: Option<redis::Client>,
    /// Built on first use and reused thereafter. `ConnectionManager` is
    /// multiplexed and reconnects on its own, so this is the pooled handle the
    /// Python client got from `redis.from_url` at construction. Initialisation
    /// is retried on the next call if Redis is down at first contact.
    manager: tokio::sync::OnceCell<redis::aio::ConnectionManager>,
    /// Set by `close`, so a post-shutdown lookup skips the tier entirely --
    /// what Python's `_available = False` did.
    closed: std::sync::atomic::AtomicBool,
    ttl: u64,
}

impl RedisCache {
    /// Connect lazily. An empty URL disables the tier entirely.
    ///
    /// Note availability is decided by configuration, not by reachability --
    /// as in Python, where `_available` is set once from the URL and never
    /// cleared on a connection error. A dead Redis therefore costs a failed
    /// round trip per lookup rather than being switched off.
    pub fn new(url: &str, ttl: u64) -> Self {
        let manager = tokio::sync::OnceCell::new();
        if url.is_empty() {
            return Self { client: None, manager, closed: Default::default(), ttl };
        }
        match redis::Client::open(url) {
            Ok(client) => Self { client: Some(client), manager, closed: Default::default(), ttl },
            Err(error) => {
                eprintln!("redis: unusable URL, L2 cache disabled: {error}");
                Self { client: None, manager, closed: Default::default(), ttl }
            }
        }
    }

    /// True when an L2 tier is configured at all.
    ///
    /// Callers use this to skip recording L2 metrics entirely when the tier is
    /// absent, so an unconfigured deployment reports no L2 series rather than
    /// a stream of misses.
    pub fn is_configured(&self) -> bool {
        self.client.is_some()
    }

    /// The shared connection, built once and reused.
    async fn connection(&self) -> Option<redis::aio::ConnectionManager> {
        if self.closed.load(std::sync::atomic::Ordering::Relaxed) {
            return None;
        }
        let client = self.client.as_ref()?;
        self.manager
            .get_or_try_init(|| {
                // Bounded hard. ConnectionManager's defaults retry six times
                // with exponential backoff, which turned one lookup against a
                // dead Redis into minutes of waiting -- this tier exists to be
                // skipped when it is unavailable, not to hold up the request.
                // A failed init leaves the cell empty, so the next lookup
                // retries; that is the reconnect path, and it must stay cheap.
                let config = redis::aio::ConnectionManagerConfig::new()
                    .set_number_of_retries(0)
                    .set_connection_timeout(CONNECT_TIMEOUT)
                    .set_response_timeout(CONNECT_TIMEOUT);
                redis::aio::ConnectionManager::new_with_config(client.clone(), config)
            })
            .await
            .inspect_err(|error| eprintln!("redis: connect failed: {error}"))
            .ok()
            .cloned()
    }

    /// Look up a key, treating every failure as a miss.
    pub async fn get(&self, key: &str) -> Option<Vec<u8>> {
        let mut connection = self.connection().await?;
        let value: Option<Vec<u8>> = connection.get(key).await
            .inspect_err(|error| eprintln!("redis: get failed: {error}"))
            .ok()?;
        value
    }

    /// Store a key with the configured TTL, ignoring failures.
    pub async fn set(&self, key: &str, value: &[u8]) {
        let Some(mut connection) = self.connection().await else {
            return;
        };
        // Redis rejects `EX 0`; Python let that error surface and swallowed it,
        // so a zero TTL stored nothing. Rounding it up to 1 second instead made
        // `REDIS_TTL=0` mean "cache for a moment" rather than "do not cache".
        if self.ttl == 0 {
            return;
        }
        let stored: Result<(), _> = connection.set_ex(key, value, self.ttl).await;
        if let Err(error) = stored {
            eprintln!("redis: set failed: {error}");
        }
    }

    /// Increment a fixed-window counter and return its new value.
    ///
    /// Not a cache operation, but it belongs on this connection: slowapi
    /// pointed its limiter at the same `REDIS_URL`, and opening a second pool
    /// to the same server to count requests would be pure overhead. `None`
    /// means Redis did not answer, which the caller treats as "fall back to
    /// the in-process counter" -- slowapi's `swallow_errors` posture.
    pub async fn incr_in_window(&self, key: &str, window_seconds: u64) -> Option<i64> {
        let mut connection = self.connection().await?;
        let count: i64 = connection.incr(key, 1).await
            .inspect_err(|error| eprintln!("redis: rate-limit incr failed: {error}"))
            .ok()?;
        // Only the request that created the window sets its expiry, so the
        // window does not slide forward with every hit inside it.
        if count == 1 {
            let expired: Result<(), _> = connection.expire(key, window_seconds.max(1) as i64).await;
            if let Err(error) = expired {
                eprintln!("redis: rate-limit expire failed: {error}");
            }
        }
        Some(count)
    }

    /// Close the shared connection, as the lifespan's `redis_cache.close()` did.
    ///
    /// `ConnectionManager` has no explicit close, so this drops it and marks the
    /// tier unusable; a lookup after shutdown then skips L2 rather than
    /// reconnecting to a server the process is finished with.
    pub async fn close(&self) {
        if let Some(manager) = self.manager.get() {
            let mut connection = manager.clone();
            // Best effort: the server drops the connection on our exit anyway,
            // and a failure here must not delay shutdown.
            let _: Result<(), _> = redis::cmd("QUIT").query_async(&mut connection).await;
        }
        self.closed.store(true, std::sync::atomic::Ordering::Relaxed);
    }

    /// How long entries live, for callers that report configuration.
    pub fn ttl(&self) -> Duration {
        Duration::from_secs(self.ttl)
    }
}

#[cfg(test)]
mod tests {
    /// L2 must degrade fast, not merely degrade.
    ///
    /// Pooling the connection initially pulled in `ConnectionManager`'s default
    /// retry schedule -- six attempts with exponential backoff -- which turned
    /// a single lookup against a dead Redis into minutes of waiting while still
    /// technically "swallowing the error".
    #[tokio::test]
    async fn an_unreachable_redis_gives_up_quickly() {
        let cache = RedisCache::new("redis://127.0.0.1:1/", 900);
        let started = std::time::Instant::now();
        assert!(cache.get("any-key").await.is_none());
        cache.set("any-key", b"value").await;
        let elapsed = started.elapsed();
        assert!(elapsed < CONNECT_TIMEOUT * 3,
                "L2 took {elapsed:?} to give up; it must stay off the critical path");
    }

    use super::*;

    #[test]
    fn an_empty_url_disables_the_tier() {
        assert!(!RedisCache::new("", 900).is_configured());
    }

    #[test]
    fn a_malformed_url_disables_the_tier_rather_than_panicking() {
        assert!(!RedisCache::new("not a url", 900).is_configured());
    }

    #[test]
    fn a_valid_url_configures_the_tier_without_connecting() {
        // Nothing is listening on this port; construction must still succeed,
        // because connection happens per operation and failures degrade.
        assert!(RedisCache::new("redis://127.0.0.1:6399/0", 900).is_configured());
    }

    #[tokio::test]
    async fn an_unreachable_redis_reads_as_a_miss() {
        let cache = RedisCache::new("redis://127.0.0.1:6399/0", 900);
        assert_eq!(cache.get("any-key").await, None);
    }

    #[tokio::test]
    async fn an_unreachable_redis_swallows_writes() {
        let cache = RedisCache::new("redis://127.0.0.1:6399/0", 900);
        // Must not panic and must not propagate.
        cache.set("any-key", b"value").await;
    }

    #[tokio::test]
    async fn a_disabled_tier_is_inert() {
        let cache = RedisCache::new("", 900);
        cache.set("k", b"v").await;
        assert_eq!(cache.get("k").await, None);
    }

    #[test]
    fn ttl_is_reported_from_configuration() {
        assert_eq!(RedisCache::new("", 900).ttl(), Duration::from_secs(900));
    }
}
