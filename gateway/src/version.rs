//! The API version prefix — NFR-10, §9.4, T-90.
//!
//! `NFR-10`: "The public API is versioned; breaking changes require a new major
//! version and a deprecation window." §9.4 writes the whole surface as
//! `/v1/...`, and the gateway served it unversioned, so there was no way to make
//! a breaking change and keep a promise.
//!
//! Every endpoint is now served twice: under `/v1` and, for the deprecation
//! window, at the root. The two spellings reach the same handler, which means
//! every path-keyed decision in the gateway has to agree about which endpoint a
//! request is for. Rate limiting must agree (or a client doubles its quota by
//! alternating, and an unstripped prefix has no bucket at all); metrics must
//! *not* (the whole point of the window is watching unversioned traffic fall to
//! zero before the sunset).
//!
//! Only the version this build serves is stripped. `/v2/route` is not an
//! endpoint here, and treating it as one would give a future version's traffic
//! this version's behaviour -- silently, and in the direction that breaks the
//! promise the version number exists to make.

/// The major version this build serves, with its leading slash.
pub const PREFIX: &str = "/v1";

/// `path` with the served version prefix removed, or `path` unchanged.
///
/// `/v1/route` becomes `/route`. `/v2/route` and `/v1` are returned unchanged:
/// the first is a version this build does not serve, and the second is the
/// prefix alone, which is not an endpoint.
pub fn strip_served_version(path: &str) -> &str {
    match path.strip_prefix(PREFIX) {
        Some(rest) if rest.starts_with('/') => rest,
        _ => path,
    }
}

/// Whether a request arrived on the deprecated unversioned surface.
pub fn is_unversioned(path: &str) -> bool {
    !path.starts_with(&format!("{PREFIX}/")) && path != PREFIX
}

/// The endpoints served under both spellings: the public API `NFR-10` versions.
///
/// `/health`, `/ready`, the metrics scrape and the docs are absent on purpose.
/// They are unversioned *by design* rather than awaiting migration -- an
/// orchestrator's liveness probe is not a client integration -- so advising
/// their callers to move to `/v1` would be advice with nowhere to go.
const VERSIONED: &[&str] = &[
    "/route", "/matrix", "/matrix-graph", "/match", "/trip", "/nearest",
    "/vrp", "/vrp/allocate",
];

/// Whether this unversioned path has a `/v1` successor to point a client at.
///
/// The deprecation advisory is only honest where the successor exists. A 404 on
/// a path the gateway never served is a client mistake, and answering it with a
/// migration notice invents an endpoint.
pub fn has_versioned_successor(path: &str) -> bool {
    VERSIONED.contains(&path) || path.starts_with("/tile/")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_served_version_is_stripped() {
        assert_eq!(strip_served_version("/v1/route"), "/route");
        assert_eq!(strip_served_version("/v1/vrp/allocate"), "/vrp/allocate");
        assert_eq!(strip_served_version("/v1/tile/driving/1/2/3.mvt"),
                   "/tile/driving/1/2/3.mvt");
    }

    #[test]
    fn an_unversioned_path_is_left_alone() {
        assert_eq!(strip_served_version("/route"), "/route");
        assert_eq!(strip_served_version("/health"), "/health");
    }

    /// A version this build does not serve must not be mistaken for one it
    /// does. Stripping any `/vN` would give future traffic today's behaviour.
    #[test]
    fn another_version_is_not_stripped() {
        assert_eq!(strip_served_version("/v2/route"), "/v2/route");
        assert_eq!(strip_served_version("/v10/route"), "/v10/route");
        assert_eq!(strip_served_version("/v/route"), "/v/route");
    }

    /// The prefix on its own is not an endpoint, and stripping it to the empty
    /// string would make it match nothing in a way that reads as a bug later.
    #[test]
    fn the_bare_prefix_is_not_an_endpoint() {
        assert_eq!(strip_served_version("/v1"), "/v1");
        assert_eq!(strip_served_version("/v1x/route"), "/v1x/route");
    }

    /// The advisory has to point somewhere. Everything listed is served under
    /// `/v1`; everything unlisted either is not an endpoint or is unversioned
    /// on purpose.
    #[test]
    fn only_endpoints_with_a_successor_are_advised() {
        for path in VERSIONED {
            assert!(has_versioned_successor(path), "{path}");
        }
        assert!(has_versioned_successor("/tile/driving/1/2/3.mvt"));

        for path in ["/health", "/ready", "/metrics", "/docs", "/redoc",
                     "/openapi.json", "/nonsense", "/"] {
            assert!(!has_versioned_successor(path),
                    "{path} would be told to migrate to an endpoint that does \
                     not exist");
        }
    }

    /// The limiter and the advisory have to cover the same endpoints, or one
    /// of them has been extended and the other forgotten.
    #[test]
    fn the_advised_set_is_the_rate_limited_set() {
        let limits = crate::ratelimit::Limits::everything_limited_for_tests();
        for path in VERSIONED {
            assert!(limits.for_path(path).is_some(),
                    "{path} is advised for migration but has no bucket");
        }
    }

    #[test]
    fn only_root_paths_count_as_unversioned() {
        assert!(is_unversioned("/route"));
        assert!(is_unversioned("/health"));
        assert!(!is_unversioned("/v1/route"));
        assert!(!is_unversioned("/v1"));
        assert!(is_unversioned("/v2/route"), "a version we do not serve is not v1");
    }
}
