//! Settings, mirroring `src/app/config.py` name for name.
//!
//! Every key in `deploy/env/app.env` must resolve here, and every setting's
//! default must equal the value committed in that file. `tests::app_env_*`
//! enforce both directions, the same contract `tests/test_config.py` enforces
//! on the Python side -- it is the cheapest correctness gate this port has.

use std::collections::HashMap;

/// A value that can be read from an environment variable.
///
/// Deliberately not `FromStr`: booleans need pydantic's spelling set rather
/// than Rust's `"true"`/`"false"`-only parse, or `DEBUG=1` would silently read
/// as false.
pub trait FromEnv: Sized {
    fn from_env_str(raw: &str) -> Option<Self>;
}

macro_rules! from_env_via_fromstr {
    ($($ty:ty),*) => {
        $(impl FromEnv for $ty {
            fn from_env_str(raw: &str) -> Option<Self> { raw.parse().ok() }
        })*
    };
}
from_env_via_fromstr!(u16, u64, usize, f64);

impl FromEnv for String {
    fn from_env_str(raw: &str) -> Option<Self> {
        Some(raw.to_string())
    }
}

impl FromEnv for bool {
    /// Accepts what pydantic-settings accepts, case-insensitively.
    fn from_env_str(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Some(true),
            "0" | "false" | "no" | "off" => Some(false),
            _ => None,
        }
    }
}

/// Read one setting, falling back to the committed default.
///
/// An unparseable value falls back rather than aborting startup: a gateway that
/// boots with a documented default is more useful at 3am than one that refuses
/// to start because a typo reached `L1_CACHE_TTL`.
fn read<T: FromEnv>(key: &str, default: &str) -> T {
    let raw = std::env::var(key).unwrap_or_default();
    let candidate = if raw.is_empty() && !default.is_empty() { default } else { &raw };
    T::from_env_str(candidate)
        .or_else(|| T::from_env_str(default))
        .expect("committed default must parse")
}

/// Declares the settings table once, so the struct, the constructor and the
/// name/default table used by the conformance tests cannot drift apart.
macro_rules! settings {
    ($( $(#[$meta:meta])* $field:ident : $ty:ty = $env:literal / $default:literal ),* $(,)?) => {
        #[derive(Debug, Clone)]
        pub struct Settings {
            $( $(#[$meta])* pub $field: $ty, )*
        }

        /// `(env name, committed default)` for every setting.
        #[allow(dead_code, reason = "read by the app.env conformance tests")]
        pub const DEFAULTS: &[(&str, &str)] = &[ $( ($env, $default), )* ];

        impl Settings {
            /// Read every setting from the process environment.
            pub fn from_env() -> Self {
                Self { $( $field: read($env, $default), )* }
            }
        }
    };
}

settings! {
    // --- Core ---
    osrm_base_url: String = "OSRM_BASE_URL" / "http://localhost:5000",
    app_name: String = "APP_NAME" / "OSRM API Gateway",
    debug: bool = "DEBUG" / "false",

    // --- Rate limiting ---
    rate_limit_route: String = "RATE_LIMIT_ROUTE" / "600/minute",
    rate_limit_matrix: String = "RATE_LIMIT_MATRIX" / "300/minute",
    rate_limit_match: String = "RATE_LIMIT_MATCH" / "600/minute",
    rate_limit_trip: String = "RATE_LIMIT_TRIP" / "300/minute",
    rate_limit_vrp: String = "RATE_LIMIT_VRP" / "100/minute",
    rate_limit_nearest: String = "RATE_LIMIT_NEAREST" / "600/minute",
    rate_limit_tile: String = "RATE_LIMIT_TILE" / "600/minute",

    // --- L2 cache (Redis) ---
    redis_url: String = "REDIS_URL" / "",
    redis_ttl: u64 = "REDIS_TTL" / "900",

    // --- L1 cache (in-process) ---
    l1_cache_ttl: u64 = "L1_CACHE_TTL" / "900",
    l1_cache_maxsize: u64 = "L1_CACHE_MAXSIZE" / "1024",

    // --- Tracing ---
    otlp_endpoint: String = "OTLP_ENDPOINT" / "",

    // --- OSRM client ---
    osrm_client_timeout: u64 = "OSRM_CLIENT_TIMEOUT" / "30",
    osrm_retry_attempts: usize = "OSRM_RETRY_ATTEMPTS" / "3",
    osrm_retry_min: u64 = "OSRM_RETRY_MIN" / "1",
    osrm_retry_max: u64 = "OSRM_RETRY_MAX" / "10",

    // --- Health check ---
    health_check_timeout: u64 = "HEALTH_CHECK_TIMEOUT" / "2",
    health_check_coords: String = "HEALTH_CHECK_COORDS" / "0,0;0,0",

    // --- VRP / matrix ---
    vrp_chunk_size: usize = "VRP_CHUNK_SIZE" / "80",
    matrix_batch_size: usize = "MATRIX_BATCH_SIZE" / "500",
    vrp_sanity_limit_m: f64 = "VRP_SANITY_LIMIT_M" / "50000.0",

    // --- VRP capacity guards ---
    vrp_max_stops: usize = "VRP_MAX_STOPS" / "2000",
    vrp_max_concurrency: usize = "VRP_MAX_CONCURRENCY" / "1",
    vrp_queue_timeout: f64 = "VRP_QUEUE_TIMEOUT" / "10.0",

    // --- Matrix capacity ---
    matrix_max_cells: usize = "MATRIX_MAX_CELLS" / "10000",

    // --- VRP chunk fan-out ---
    vrp_chunk_concurrency: usize = "VRP_CHUNK_CONCURRENCY" / "4",

    // --- Metrics ---
    metrics_endpoint: String = "METRICS_ENDPOINT" / "/metrics",

    // --- Logging ---
    append_to_stderr: bool = "APPEND_TO_STDERR" / "false",

    // --- Runtime-only ---
    // Absent from app.env by design: both deployments set these per-instance,
    // and the jail can only pass these three through `${name}_env` (rc.subr
    // expands it unquoted, and HEALTH_CHECK_COORDS contains a `;`).
    host: String = "HOST" / "127.0.0.1",
    port: u16 = "PORT" / "8000",
    workers: usize = "WORKERS" / "1",
    // Replaces uvicorn's --forwarded-allow-ips, which deploy/docker/entrypoint.sh
    // passes today. A Rust binary has no entrypoint script, so the limiter's
    // trusted-proxy policy has to live here instead.
    forwarded_allow_ips: String = "FORWARDED_ALLOW_IPS" / "",
}

/// Keys present in `deploy/env/app.env` that this port deliberately does not
/// implement, with the reason. Python accepts them because `Settings` sets
/// `extra="ignore"`; this port accepts them the same way, by ignoring unknown
/// keys entirely. Listed here so the conformance test stays meaningful rather
/// than simply not checking.
#[allow(dead_code, reason = "read by the app.env conformance tests")]
pub const INTENTIONALLY_UNUSED: &[(&str, &str)] = &[
    ("OSRM_API_URL", "declared in config.py but read nowhere in src/"),
    ("REDIS_MAXSIZE", "stored on RedisCache and never used; Redis evicts server-side via maxmemory-policy"),
    ("VRP_HYSTERESIS_M", "shadowed by VrpRequest.hysteresis_m, which hardcodes its own 2000.0 default"),
];

/// Load `.env` from the working directory the way pydantic-settings does.
///
/// Two rules matter, and the obvious crate gets both wrong for this file:
/// **later duplicates win**, because `deploy/freebsd/install.sh` appends a
/// deployment overlay after the shared block and expects it to override
/// (`dotenvy` keeps the *first* occurrence, silently ignoring that overlay);
/// and **a real environment variable beats the file**, so the rc.d script and
/// compose `environment:` still have the last word. Shell sourcing is not an
/// option either -- `HEALTH_CHECK_COORDS=0,0;0,0` would break it.
///
/// A missing file is not an error: the Docker path passes everything through
/// the environment instead.
pub fn load_dotenv(path: &str) {
    let Ok(contents) = std::fs::read_to_string(path) else {
        return;
    };
    for (key, value) in parse_dotenv(&contents) {
        if std::env::var_os(&key).is_none() {
            std::env::set_var(key, value);
        }
    }
}

/// Parse dotenv contents into last-wins key/value pairs.
pub fn parse_dotenv(contents: &str) -> HashMap<String, String> {
    let mut values = HashMap::new();
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
    values
}

#[cfg(test)]
mod tests {
    use super::*;

    fn app_env() -> HashMap<String, String> {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../deploy/env/app.env");
        parse_dotenv(&std::fs::read_to_string(path).expect("deploy/env/app.env must exist"))
    }

    /// Every key in the shared env file is either a setting or a documented omission.
    #[test]
    fn app_env_keys_are_all_known() {
        for key in app_env().keys() {
            let known = DEFAULTS.iter().any(|(name, _)| name == key)
                || INTENTIONALLY_UNUSED.iter().any(|(name, _)| name == key);
            assert!(known, "{key} is in app.env but unknown to this port");
        }
    }

    /// Every setting that belongs in the shared env file is in it. HOST/PORT/
    /// WORKERS/FORWARDED_ALLOW_IPS are excluded by design -- see the struct.
    #[test]
    fn every_shared_setting_is_in_app_env() {
        let runtime_only = ["HOST", "PORT", "WORKERS", "FORWARDED_ALLOW_IPS"];
        let file = app_env();
        for (name, _) in DEFAULTS {
            if runtime_only.contains(name) {
                continue;
            }
            assert!(file.contains_key(*name), "{name} is a setting but missing from app.env");
        }
    }

    /// The committed values are the code defaults. Drift here means a deployment
    /// silently behaves differently from a bare `cargo run`.
    #[test]
    fn app_env_values_match_defaults() {
        let file = app_env();
        for (name, default) in DEFAULTS {
            let Some(committed) = file.get(*name) else { continue };
            assert_eq!(committed, default, "{name} differs between app.env and its default");
        }
    }

    #[test]
    fn dotenv_last_duplicate_wins() {
        // install.sh appends its deployment overlay after the shared block and
        // relies on this; dotenvy would keep the first and ignore the overlay.
        let parsed = parse_dotenv("OSRM_BASE_URL=http://shared:5000\nOSRM_BASE_URL=http://overlay:5000\n");
        assert_eq!(parsed["OSRM_BASE_URL"], "http://overlay:5000");
    }

    #[test]
    fn dotenv_skips_comments_and_blanks() {
        let parsed = parse_dotenv("# comment\n\nA=1\n  \nB=2\n");
        assert_eq!(parsed.len(), 2);
    }

    #[test]
    fn dotenv_keeps_semicolons_and_strips_quotes() {
        let parsed = parse_dotenv("HEALTH_CHECK_COORDS=0,0;0,0\nAPP_NAME=\"OSRM API Gateway\"\n");
        assert_eq!(parsed["HEALTH_CHECK_COORDS"], "0,0;0,0");
        assert_eq!(parsed["APP_NAME"], "OSRM API Gateway");
    }

    #[test]
    fn bool_accepts_pydantic_spellings() {
        for raw in ["1", "true", "TRUE", "yes", "on"] {
            assert_eq!(bool::from_env_str(raw), Some(true), "{raw}");
        }
        for raw in ["0", "false", "FALSE", "no", "off"] {
            assert_eq!(bool::from_env_str(raw), Some(false), "{raw}");
        }
        assert_eq!(bool::from_env_str("maybe"), None);
    }
}
