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

pub struct RedisCache {
    client: Option<redis::Client>,
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
        if url.is_empty() {
            return Self { client: None, ttl };
        }
        match redis::Client::open(url) {
            Ok(client) => Self { client: Some(client), ttl },
            Err(error) => {
                eprintln!("redis: unusable URL, L2 cache disabled: {error}");
                Self { client: None, ttl }
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

    /// Look up a key, treating every failure as a miss.
    pub async fn get(&self, key: &str) -> Option<Vec<u8>> {
        let client = self.client.as_ref()?;
        let mut connection = client.get_multiplexed_async_connection().await
            .inspect_err(|error| eprintln!("redis: connect failed on get: {error}"))
            .ok()?;
        let value: Option<Vec<u8>> = connection.get(key).await
            .inspect_err(|error| eprintln!("redis: get failed: {error}"))
            .ok()?;
        value
    }

    /// Store a key with the configured TTL, ignoring failures.
    pub async fn set(&self, key: &str, value: &[u8]) {
        let Some(client) = self.client.as_ref() else {
            return;
        };
        let Ok(mut connection) = client.get_multiplexed_async_connection().await
            .inspect_err(|error| eprintln!("redis: connect failed on set: {error}"))
        else {
            return;
        };
        let stored: Result<(), _> = connection
            .set_ex(key, value, self.ttl.max(1))
            .await;
        if let Err(error) = stored {
            eprintln!("redis: set failed: {error}");
        }
    }

    /// How long entries live, for callers that report configuration.
    pub fn ttl(&self) -> Duration {
        Duration::from_secs(self.ttl)
    }
}

#[cfg(test)]
mod tests {
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
