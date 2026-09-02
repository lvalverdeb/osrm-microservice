//! Per-client rate limiting.
//!
//! A fixed window, which is what slowapi's underlying `limits` library uses by
//! default, so `600/minute` means 600 requests per wall-clock minute rather than
//! a rolling average.
//!
//! Keying is the subtle part. Behind a reverse proxy the TCP peer is the proxy,
//! so every client would share one bucket. `X-Forwarded-For` fixes that, but
//! only for peers the operator trusts -- otherwise any client could spoof a
//! header and get a fresh allowance per request. The resolution below mirrors
//! uvicorn's `ProxyHeadersMiddleware`, which the Python deployment relies on:
//! walk the forwarded chain from the right and stop at the first host outside
//! the trusted set, so a value the client prepended is skipped in favour of
//! what the proxy appended.

use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

/// Above this many tracked buckets, stale windows are pruned on write.
const PRUNE_THRESHOLD: usize = 10_000;

/// A parsed `N/unit` limit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limit {
    pub amount: u32,
    pub window_seconds: u64,
    multiple: u32,
    granularity: &'static str,
}

impl Limit {
    /// Parse a limit as `limits` spells it: `600/minute`, `5/2minutes`.
    ///
    /// An unparseable value yields None; callers treat that as "no limit"
    /// rather than refusing to start, matching how the rest of the settings
    /// fall back rather than abort.
    pub fn parse(raw: &str) -> Option<Self> {
        let (amount, per) = raw.split_once('/')?;
        let amount: u32 = amount.trim().parse().ok()?;
        let per = per.trim();
        let split = per.find(|c: char| c.is_alphabetic())?;
        let multiple: u32 = if split == 0 { 1 } else { per[..split].parse().ok()? };
        let (granularity, seconds) = match per[split..].trim_end_matches('s') {
            "second" => ("second", 1),
            "minute" => ("minute", 60),
            "hour" => ("hour", 3600),
            "day" => ("day", 86_400),
            _ => return None,
        };
        Some(Limit { amount, window_seconds: seconds * multiple as u64, multiple, granularity })
    }

    /// Render as slowapi's 429 body does: `"2 per 1 minute"`.
    pub fn describe(&self) -> String {
        format!("{} per {} {}", self.amount, self.multiple, self.granularity)
    }
}

/// One configured limit: absent, valid, or a startup failure.
///
/// An empty value means "unset", as it does for every other setting; anything
/// else must parse, because the alternative is an endpoint that comes up
/// silently unlimited.
fn configured(name: &str, raw: &str) -> Result<Option<Limit>, String> {
    if raw.trim().is_empty() {
        return Ok(None);
    }
    Limit::parse(raw)
        .map(Some)
        .ok_or_else(|| format!("{name}: cannot parse rate limit {raw:?}"))
}

/// Which peers may set `X-Forwarded-For`.
#[derive(Debug, Clone, Default)]
pub struct TrustedProxies {
    trust_all: bool,
    exact: Vec<IpAddr>,
    networks: Vec<(IpAddr, u8)>,
}

impl TrustedProxies {
    /// Parse the comma-separated form both deployments already use.
    ///
    /// Accepts bare addresses, CIDR blocks, and `*` for "trust every peer" --
    /// which the entrypoint warns about, because it lets any client set its own
    /// limiter key.
    pub fn parse(raw: &str) -> Self {
        let mut trusted = TrustedProxies::default();
        for entry in raw.split(',').map(str::trim).filter(|e| !e.is_empty()) {
            if entry == "*" {
                trusted.trust_all = true;
            } else if let Some((network, bits)) = entry.split_once('/') {
                if let (Ok(addr), Ok(bits)) = (network.parse::<IpAddr>(), bits.parse::<u8>()) {
                    trusted.networks.push((addr, bits));
                }
            } else if let Ok(addr) = entry.parse::<IpAddr>() {
                trusted.exact.push(addr);
            }
        }
        trusted
    }

    /// True when this host may speak for its clients.
    pub fn contains(&self, host: &str) -> bool {
        if self.trust_all {
            return true;
        }
        let Ok(addr) = host.trim().parse::<IpAddr>() else {
            return false;
        };
        self.exact.contains(&addr)
            || self.networks.iter().any(|(network, bits)| in_network(addr, *network, *bits))
    }

    /// True when nothing is trusted, i.e. forwarded headers are ignored.
    pub fn is_empty(&self) -> bool {
        !self.trust_all && self.exact.is_empty() && self.networks.is_empty()
    }
}

/// Whether `addr` falls inside `network/bits`.
fn in_network(addr: IpAddr, network: IpAddr, bits: u8) -> bool {
    let (addr, network) = match (addr, network) {
        (IpAddr::V4(a), IpAddr::V4(n)) => (a.octets().to_vec(), n.octets().to_vec()),
        (IpAddr::V6(a), IpAddr::V6(n)) => (a.octets().to_vec(), n.octets().to_vec()),
        _ => return false,
    };
    let mut remaining = bits as usize;
    for (a, n) in addr.iter().zip(network.iter()) {
        if remaining == 0 {
            return true;
        }
        let take = remaining.min(8);
        let mask = if take == 8 { 0xFFu8 } else { !(0xFFu8 >> take) };
        if a & mask != n & mask {
            return false;
        }
        remaining -= take;
    }
    true
}

/// Resolve the address a limit should be keyed on.
///
/// Args:
///     peer: The immediate TCP peer.
///     forwarded_for: The `X-Forwarded-For` header, if present.
///     trusted: Peers permitted to set that header.
///
/// Returns:
///     The closest untrusted hop, or the peer when nothing is trusted.
pub fn client_key(peer: &str, forwarded_for: Option<&str>, trusted: &TrustedProxies) -> String {
    if trusted.is_empty() || !trusted.contains(peer) {
        return peer.to_string();
    }
    let Some(chain) = forwarded_for else {
        return peer.to_string();
    };
    let hops: Vec<&str> = chain.split(',').map(str::trim).filter(|h| !h.is_empty()).collect();
    if hops.is_empty() {
        return peer.to_string();
    }
    // Right to left: the first hop we do not trust is the closest thing to a
    // real client. Anything further left was supplied by that client.
    hops.iter()
        .rev()
        .find(|hop| !trusted.contains(hop))
        .map(|hop| hop.to_string())
        // Every hop is trusted, so the leftmost is as close to the origin as
        // this chain gets.
        .unwrap_or_else(|| hops[0].to_string())
}

#[derive(Debug, Clone, Copy)]
struct Bucket {
    window_id: u64,
    count: u32,
}

/// Fixed-window counters, one bucket per (client, endpoint, window).
pub struct RateLimiter {
    buckets: Mutex<HashMap<String, Bucket>>,
}

impl RateLimiter {
    pub fn new() -> Self {
        Self { buckets: Mutex::new(HashMap::new()) }
    }

    /// Record one request and report whether it is allowed.
    pub fn check(&self, client: &str, endpoint: &str, limit: Limit) -> bool {
        self.check_at(client, endpoint, limit, now_seconds())
    }

    /// The same, at an explicit time -- the seam the tests drive.
    /// Check against Redis when it is configured, falling back in process.
    ///
    /// slowapi took a `storage_uri` and shared one allowance across every
    /// worker and node, with `in_memory_fallback_enabled` and `swallow_errors`
    /// covering an outage. Counting only in process meant N instances behind a
    /// balancer allowed N times the configured limit -- the limit silently
    /// became a per-instance one.
    pub async fn check_shared(&self, redis: Option<&crate::redis_cache::RedisCache>,
                              client: &str, endpoint: &str, limit: Limit) -> bool {
        if limit.amount == 0 {
            return false;
        }
        if let Some(redis) = redis.filter(|redis| redis.is_configured()) {
            let window = limit.window_seconds.max(1);
            // Same window identity as the local path, so an instance that
            // falls back mid-window keeps counting in the same bucket.
            let window_id = now_seconds() / window;
            let key = format!("ratelimit:{client}:{endpoint}:{window_id}");
            if let Some(count) = redis.incr_in_window(&key, window).await {
                return count <= i64::from(limit.amount);
            }
        }
        self.check(client, endpoint, limit)
    }

    fn check_at(&self, client: &str, endpoint: &str, limit: Limit, now: u64) -> bool {
        if limit.amount == 0 {
            return false;
        }
        let window_id = now / limit.window_seconds.max(1);
        let key = format!("{client}|{endpoint}");
        // Recover rather than propagate: a thread that panicked while holding
        // this lock would otherwise turn one failure into a panic on every
        // subsequent request through the limiter.
        let mut buckets = match self.buckets.lock() {
            Ok(buckets) => buckets,
            Err(poisoned) => poisoned.into_inner(),
        };

        if buckets.len() > PRUNE_THRESHOLD {
            buckets.retain(|_, bucket| bucket.window_id >= window_id);
        }
        let bucket = buckets.entry(key).or_insert(Bucket { window_id, count: 0 });
        if bucket.window_id != window_id {
            *bucket = Bucket { window_id, count: 0 };
        }
        bucket.count += 1;
        bucket.count <= limit.amount
    }
}

impl Default for RateLimiter {
    fn default() -> Self {
        Self::new()
    }
}

fn now_seconds() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    const MINUTE: Limit = Limit { amount: 2, window_seconds: 60, multiple: 1,
                                  granularity: "minute" };

    #[test]
    fn limits_parse_the_deployed_spellings() {
        assert_eq!(Limit::parse("600/minute").unwrap().amount, 600);
        assert_eq!(Limit::parse("600/minute").unwrap().window_seconds, 60);
        assert_eq!(Limit::parse("100/hour").unwrap().window_seconds, 3600);
        assert_eq!(Limit::parse("5/second").unwrap().window_seconds, 1);
        assert_eq!(Limit::parse("5/2minutes").unwrap().window_seconds, 120);
    }

    #[test]
    fn the_parser_reports_spellings_it_cannot_read() {
        assert!(Limit::parse("nonsense").is_none());
        assert!(Limit::parse("600/fortnight").is_none());
    }

    /// A typo must stop the process, not silently unlimit the endpoint.
    ///
    /// `limits` raised at decoration time, so the Python app would not import
    /// with a bad value. Mapping it to `None` here made it "no limit".
    #[test]
    fn a_malformed_configured_limit_is_a_startup_failure() {
        assert!(configured("RATE_LIMIT_VRP", "100/fortnight").is_err());
        assert!(configured("RATE_LIMIT_VRP", "nonsense").is_err());
        let error = configured("RATE_LIMIT_VRP", "nonsense").unwrap_err();
        assert!(error.contains("RATE_LIMIT_VRP"), "the message must name the setting: {error}");
        // Unset stays unset.
        assert_eq!(configured("RATE_LIMIT_VRP", "").expect("empty is unset"), None);
        assert_eq!(configured("RATE_LIMIT_VRP", "  ").expect("blank is unset"), None);
        assert!(configured("RATE_LIMIT_VRP", "100/minute").expect("valid").is_some());
    }

    /// Pinned against a live slowapi response: `{"error":"Rate limit exceeded:
    /// 2 per 1 minute"}`.
    #[test]
    fn description_matches_slowapis_wording() {
        assert_eq!(Limit::parse("2/minute").unwrap().describe(), "2 per 1 minute");
        assert_eq!(Limit::parse("600/minute").unwrap().describe(), "600 per 1 minute");
    }

    #[test]
    fn requests_are_allowed_up_to_the_limit_then_refused() {
        let limiter = RateLimiter::new();
        let allowed: Vec<bool> = (0..4)
            .map(|_| limiter.check_at("client", "/nearest", MINUTE, 1000))
            .collect();
        assert_eq!(allowed, [true, true, false, false]);
    }

    #[test]
    fn the_allowance_resets_in_the_next_window() {
        let limiter = RateLimiter::new();
        assert!(limiter.check_at("c", "/x", MINUTE, 1000));
        assert!(limiter.check_at("c", "/x", MINUTE, 1000));
        assert!(!limiter.check_at("c", "/x", MINUTE, 1000));
        // 1060 lands in the next 60-second window.
        assert!(limiter.check_at("c", "/x", MINUTE, 1060));
    }

    #[test]
    fn clients_and_endpoints_get_separate_buckets() {
        let limiter = RateLimiter::new();
        assert!(limiter.check_at("a", "/x", MINUTE, 0));
        assert!(limiter.check_at("a", "/x", MINUTE, 0));
        assert!(!limiter.check_at("a", "/x", MINUTE, 0));
        // A different client, and the same client on a different endpoint.
        assert!(limiter.check_at("b", "/x", MINUTE, 0));
        assert!(limiter.check_at("a", "/y", MINUTE, 0));
    }

    fn trusted(raw: &str) -> TrustedProxies {
        TrustedProxies::parse(raw)
    }

    /// The default posture: a spoofed header must not change the key.
    #[test]
    fn forwarded_for_is_ignored_when_nothing_is_trusted() {
        assert_eq!(client_key("127.0.0.1", Some("203.0.113.7"), &trusted("")), "127.0.0.1");
    }

    #[test]
    fn forwarded_for_is_ignored_when_the_peer_is_not_trusted() {
        assert_eq!(client_key("127.0.0.1", Some("203.0.113.7"), &trusted("10.0.0.0/8")),
                   "127.0.0.1");
    }

    #[test]
    fn forwarded_for_is_honoured_when_the_peer_is_trusted() {
        assert_eq!(client_key("127.0.0.1", Some("203.0.113.7"), &trusted("127.0.0.1")),
                   "203.0.113.7");
    }

    /// A client can prepend anything; the closest untrusted hop wins.
    #[test]
    fn a_client_supplied_hop_does_not_win() {
        let key = client_key("127.0.0.1", Some("1.2.3.4, 203.0.113.7, 10.0.0.9"),
                             &trusted("127.0.0.1,10.0.0.9"));
        assert_eq!(key, "203.0.113.7");
    }

    #[test]
    fn a_fully_trusted_chain_falls_back_to_the_leftmost_hop() {
        let key = client_key("127.0.0.1", Some("203.0.113.7, 10.0.0.9"),
                             &trusted("*"));
        assert_eq!(key, "203.0.113.7");
    }

    #[test]
    fn a_trusted_peer_without_the_header_keys_on_itself() {
        assert_eq!(client_key("127.0.0.1", None, &trusted("127.0.0.1")), "127.0.0.1");
    }

    #[test]
    fn cidr_membership_is_honoured() {
        assert!(trusted("10.0.0.0/8").contains("10.1.2.3"));
        assert!(!trusted("10.0.0.0/8").contains("11.1.2.3"));
        assert!(trusted("192.168.1.0/24").contains("192.168.1.99"));
        assert!(!trusted("192.168.1.0/24").contains("192.168.2.1"));
    }

    #[test]
    fn a_wildcard_trusts_every_peer() {
        assert!(trusted("*").contains("203.0.113.7"));
    }

    #[test]
    fn non_addresses_are_never_trusted() {
        assert!(!trusted("127.0.0.1").contains("not-an-ip"));
    }
}

/// The per-endpoint limits, resolved once at startup.
///
/// `/health`, `/ready` and `/metrics` are deliberately absent: the Python
/// gateway limits per decorator and never decorates them, so probes and scrapes
/// are never shed.
#[derive(Debug, Clone)]
pub struct Limits {
    route: Option<Limit>,
    matrix: Option<Limit>,
    match_trace: Option<Limit>,
    trip: Option<Limit>,
    nearest: Option<Limit>,
    tile: Option<Limit>,
    vrp: Option<Limit>,
}

impl Limits {
    /// Parse every configured limit, refusing to start if one is malformed.
    ///
    /// `limits` raises at decoration time, so a typo in a `RATE_LIMIT_*` value
    /// stopped the Python app from importing. Mapping the same typo to `None`
    /// here meant "no limit at all" -- the endpoint came up silently unlimited,
    /// which is the one failure mode a rate limiter must not have.
    pub fn from_settings(settings: &crate::config::Settings) -> Result<Self, String> {
        Ok(Self {
            route: configured("RATE_LIMIT_ROUTE", &settings.rate_limit_route)?,
            matrix: configured("RATE_LIMIT_MATRIX", &settings.rate_limit_matrix)?,
            match_trace: configured("RATE_LIMIT_MATCH", &settings.rate_limit_match)?,
            trip: configured("RATE_LIMIT_TRIP", &settings.rate_limit_trip)?,
            nearest: configured("RATE_LIMIT_NEAREST", &settings.rate_limit_nearest)?,
            tile: configured("RATE_LIMIT_TILE", &settings.rate_limit_tile)?,
            vrp: configured("RATE_LIMIT_VRP", &settings.rate_limit_vrp)?,
        })
    }

    /// Every endpoint limited, for tests that check path coverage rather than
    /// the limits themselves.
    #[cfg(test)]
    pub fn everything_limited_for_tests() -> Self {
        let limit = Limit::parse("600/minute");
        Self {
            route: limit, matrix: limit, match_trace: limit, trip: limit,
            nearest: limit, tile: limit, vrp: limit,
        }
    }

    /// The limit for a request path, with the bucket label to key it on.
    ///
    /// The label matters for `/tile`, whose path carries the tile coordinates:
    /// keying on the raw path would give every tile its own allowance, where
    /// slowapi keys on the route function and gives them one between them.
    pub fn for_path(&self, path: &str) -> Option<(&'static str, Limit)> {
        // NFR-10/T-90: `/v1/route` is `/route`, and shares its allowance. A
        // separate bucket per spelling would double every client's quota for
        // the cost of alternating the prefix, and no bucket at all -- which is
        // what exact matching gave before the prefix was stripped here -- would
        // serve the same handler unlimited.
        let path = crate::version::strip_served_version(path);
        let (label, limit) = match path {
            "/route" => ("route", self.route),
            "/matrix" => ("matrix", self.matrix),
            "/matrix-graph" => ("matrix-graph", self.matrix),
            "/match" => ("match", self.match_trace),
            "/trip" => ("trip", self.trip),
            "/nearest" => ("nearest", self.nearest),
            "/vrp" => ("vrp", self.vrp),
            "/vrp/allocate" => ("vrp-allocate", self.vrp),
            _ if path.starts_with("/tile/") => ("tile", self.tile),
            _ => return None,
        };
        limit.map(|limit| (label, limit))
    }
}

#[cfg(test)]
mod shared_tests {
    use super::*;
    use crate::redis_cache::RedisCache;

    fn limit() -> Limit {
        Limit::parse("2/minute").expect("a valid spelling")
    }

    /// With no Redis configured the shared path must behave exactly as the
    /// in-process one, not open up into "unlimited".
    #[tokio::test]
    async fn an_unconfigured_redis_falls_back_to_the_local_counter() {
        let limiter = RateLimiter::new();
        let l2 = RedisCache::new("", 900);
        assert!(limiter.check_shared(Some(&l2), "1.2.3.4", "route", limit()).await);
        assert!(limiter.check_shared(Some(&l2), "1.2.3.4", "route", limit()).await);
        assert!(!limiter.check_shared(Some(&l2), "1.2.3.4", "route", limit()).await,
                "the third request in the window must be refused");
    }

    /// slowapi's `swallow_errors` + `in_memory_fallback_enabled`: an outage
    /// degrades to per-instance counting, it does not stop limiting.
    #[tokio::test]
    async fn an_unreachable_redis_still_enforces_the_limit_locally() {
        let limiter = RateLimiter::new();
        let l2 = RedisCache::new("redis://127.0.0.1:1/", 900);
        assert!(limiter.check_shared(Some(&l2), "5.6.7.8", "vrp", limit()).await);
        assert!(limiter.check_shared(Some(&l2), "5.6.7.8", "vrp", limit()).await);
        assert!(!limiter.check_shared(Some(&l2), "5.6.7.8", "vrp", limit()).await);
    }
}

#[cfg(test)]
mod limits_tests {
    use super::*;

    fn limits() -> Limits {
        Limits {
            route: Limit::parse("600/minute"),
            matrix: Limit::parse("300/minute"),
            match_trace: Limit::parse("600/minute"),
            trip: Limit::parse("300/minute"),
            nearest: Limit::parse("600/minute"),
            tile: Limit::parse("600/minute"),
            vrp: Limit::parse("100/minute"),
        }
    }

    #[test]
    fn probes_and_scrapes_are_never_limited() {
        for path in ["/health", "/ready", "/metrics", "/unknown"] {
            assert!(limits().for_path(path).is_none(), "{path}");
        }
    }

    #[test]
    fn matrix_and_matrix_graph_share_a_setting_but_not_a_bucket() {
        let (matrix_label, matrix) = limits().for_path("/matrix").unwrap();
        let (graph_label, graph) = limits().for_path("/matrix-graph").unwrap();
        assert_eq!(matrix, graph);
        assert_ne!(matrix_label, graph_label);
    }

    /// Every tile shares one allowance, as slowapi's per-function keying gives.
    #[test]
    fn all_tiles_share_one_bucket() {
        let a = limits().for_path("/tile/driving/12/100/200.mvt").unwrap();
        let b = limits().for_path("/tile/driving/12/101/201.mvt").unwrap();
        assert_eq!(a.0, b.0);
        assert_eq!(a.0, "tile");
    }

    #[test]
    fn vrp_endpoints_share_the_setting_and_split_the_buckets() {
        assert_eq!(limits().for_path("/vrp").unwrap().0, "vrp");
        assert_eq!(limits().for_path("/vrp/allocate").unwrap().0, "vrp-allocate");
        assert_eq!(limits().for_path("/vrp").unwrap().1,
                   limits().for_path("/vrp/allocate").unwrap().1);
    }

    // ---------------------------------------------------------------- NFR-10
    // T-90 serves every endpoint under `/v1` as well as at the root. Both
    // spellings reach the same handler, so every path-keyed decision has to
    // agree about that -- and this is the one where disagreeing is a hole
    // rather than an inconsistency.

    /// The bug this exists to prevent: `for_path` matched exact strings, so
    /// routing `/v1/route` without touching it would have served the same
    /// handler with no limit at all.
    #[test]
    fn a_versioned_path_is_limited_at_all() {
        assert!(limits().for_path("/v1/route").is_some(),
                "/v1/route has no bucket, so the versioned surface is unlimited");
    }

    /// One allowance between them. Separate buckets would hand any client
    /// twice its quota for the cost of alternating the prefix.
    #[test]
    fn both_spellings_of_an_endpoint_share_one_bucket() {
        for path in ["/route", "/matrix", "/matrix-graph", "/match", "/trip",
                     "/nearest", "/vrp", "/vrp/allocate",
                     "/tile/driving/12/100/200.mvt"] {
            let plain = limits().for_path(path);
            let versioned = limits().for_path(&format!("/v1{path}"));
            assert_eq!(plain.map(|(label, _)| label),
                       versioned.map(|(label, _)| label),
                       "{path} and /v1{path} are not the same bucket");
            assert_eq!(plain.map(|(_, limit)| limit),
                       versioned.map(|(_, limit)| limit), "{path}");
        }
    }

    /// A version the gateway does not serve must not borrow v1's allowance,
    /// and the unlimited paths stay unlimited under the prefix.
    #[test]
    fn only_the_version_that_is_served_gets_the_buckets() {
        assert!(limits().for_path("/v2/route").is_none(),
                "an unserved version borrowed v1's bucket");
        assert!(limits().for_path("/v1/health").is_none());
        assert!(limits().for_path("/v1/nonsense").is_none());
        assert!(limits().for_path("/v1").is_none());
    }
}
