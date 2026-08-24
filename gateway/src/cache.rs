//! Cache keys, byte-compatible with `src/app/services/cache.py`.
//!
//! The key must match Python's exactly, because both gateways are deployed
//! against the same Redis during the transition: a mismatch does not break
//! anything, it just silently gives each implementation its own L2 namespace,
//! so a rollback starts cold and side-by-side runs never share a hit.
//!
//! Python builds it as
//! `f"{endpoint}:{sha256(json.dumps(params, sort_keys=True, default=str))}"`,
//! and three details of `json.dumps` differ from `serde_json`'s defaults:
//! separators are `", "` and `": "` rather than `","` and `":"`; non-ASCII is
//! escaped (`ensure_ascii=True`); and floats use Python's `repr`, which writes
//! `1e-07` where Rust writes `1e-7` and `50.0` where Rust writes `50`.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use sha2::{Digest, Sha256};

use crate::pyfloat::python_float;

/// A query-parameter value, typed because Python's dict is: `number` is sent as
/// an int and `fallback_speed` as a float, and both serialise differently from
/// the same value spelled as a string.
#[derive(Debug, Clone, PartialEq)]
pub enum ParamValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    Null,
}

impl From<&str> for ParamValue {
    fn from(v: &str) -> Self {
        ParamValue::Str(v.to_string())
    }
}

impl From<String> for ParamValue {
    fn from(v: String) -> Self {
        ParamValue::Str(v)
    }
}

impl From<i64> for ParamValue {
    fn from(v: i64) -> Self {
        ParamValue::Int(v)
    }
}

impl From<f64> for ParamValue {
    fn from(v: f64) -> Self {
        ParamValue::Float(v)
    }
}

impl From<bool> for ParamValue {
    fn from(v: bool) -> Self {
        ParamValue::Bool(v)
    }
}

/// Upstream query parameters, in Python dict order.
///
/// Insertion-ordered rather than sorted, because the query string must match
/// Python's byte for byte: httpx emits parameters in dict order, and replay
/// fixtures key on the upstream URL. The cache key sorts separately, which is
/// what `json.dumps(..., sort_keys=True)` does on the Python side.
///
/// Re-inserting an existing key replaces the value but keeps the key's original
/// position, exactly as `dict.update` does -- which is what puts `/match`'s
/// caller-supplied `radiuses` where the breadcrumb-derived one was.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Params(Vec<(String, ParamValue)>);

impl Params {
    pub fn new() -> Self {
        Self(Vec::new())
    }

    /// Insert or replace, preserving an existing key's position.
    pub fn insert(&mut self, key: impl Into<String>, value: impl Into<ParamValue>) {
        let key = key.into();
        let value = value.into();
        match self.0.iter_mut().find(|(existing, _)| *existing == key) {
            Some(slot) => slot.1 = value,
            None => self.0.push((key, value)),
        }
    }

    pub fn get(&self, key: &str) -> Option<&ParamValue> {
        self.0.iter().find(|(existing, _)| existing == key).map(|(_, value)| value)
    }

    #[allow(dead_code, reason = "used by the param-construction tests")]
    pub fn contains_key(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    /// Merge `other` over self, letting its values win.
    pub fn extend(&mut self, other: Params) {
        for (key, value) in other.0 {
            self.insert(key, value);
        }
    }

    /// Iterate in insertion order, as the query string needs.
    pub fn iter(&self) -> impl Iterator<Item = (&String, &ParamValue)> {
        self.0.iter().map(|(key, value)| (key, value))
    }

    /// Iterate key-sorted, as the cache key needs.
    fn sorted(&self) -> BTreeMap<&String, &ParamValue> {
        self.0.iter().map(|(key, value)| (key, value)).collect()
    }
}

impl std::ops::Index<&str> for Params {
    type Output = ParamValue;

    fn index(&self, key: &str) -> &ParamValue {
        self.get(key).unwrap_or_else(|| panic!("missing param {key}"))
    }
}

/// Serialise one value the way `json.dumps` would.
fn dump_value(value: &ParamValue, out: &mut String) {
    match value {
        ParamValue::Str(s) => dump_string(s, out),
        ParamValue::Int(i) => {
            let _ = write!(out, "{i}");
        }
        ParamValue::Float(f) => out.push_str(&python_float(*f)),
        ParamValue::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        ParamValue::Null => out.push_str("null"),
    }
}

/// Escape a string the way `json.dumps` does with `ensure_ascii=True`.
fn dump_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c if c.is_ascii() => out.push(c),
            // ensure_ascii=True: astral characters become a surrogate pair,
            // which is what Python emits and what a byte-equal key requires.
            c => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    out.push('"');
}

/// Serialise params exactly as `json.dumps(params, sort_keys=True, default=str)`.
pub fn dump_params(params: &Params) -> String {
    let mut out = String::from("{");
    for (index, (key, value)) in params.sorted().into_iter().enumerate() {
        if index > 0 {
            out.push_str(", ");
        }
        dump_string(key, &mut out);
        out.push_str(": ");
        dump_value(value, &mut out);
    }
    out.push('}');
    out
}

/// Build the cache key for one upstream call.
///
/// The endpoint stays in plaintext as a prefix and only the params are hashed,
/// matching Python. Note the endpoint carries the coordinates, so keys are not
/// safe to log wholesale.
pub fn build_cache_key(endpoint: &str, params: &Params) -> String {
    let digest = Sha256::digest(dump_params(params).as_bytes());
    format!("{endpoint}:{digest:x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params(pairs: &[(&str, ParamValue)]) -> Params {
        let mut built = Params::new();
        for (key, value) in pairs {
            built.insert(*key, value.clone());
        }
        built
    }

    #[test]
    fn empty_params_match_python() {
        assert_eq!(dump_params(&Params::new()), "{}");
    }

    /// Pins the three divergences from serde_json at once: the `", "` / `": "`
    /// separators, key sorting, and int-vs-string typing.
    #[test]
    fn separators_and_sorting_match_python() {
        let p = params(&[("b", ParamValue::Int(2)), ("a", "1".into())]);
        assert_eq!(dump_params(&p), r#"{"a": "1", "b": 2}"#);
    }

    #[test]
    fn strings_are_escaped_as_ascii() {
        let mut out = String::new();
        dump_string("a\"b\\c\nd", &mut out);
        assert_eq!(out, r#""a\"b\\c\nd""#);

        // ensure_ascii=True: Python escapes non-ASCII rather than emitting UTF-8.
        let mut accented = String::new();
        dump_string("Ca\u{f1}as", &mut accented);
        assert_eq!(accented, r#""Ca\u00f1as""#);

        // Astral characters become a surrogate pair, as Python emits them.
        let mut astral = String::new();
        dump_string("\u{1f600}", &mut astral);
        assert_eq!(astral, r#""\ud83d\ude00""#);
    }

    /// Insertion order drives the query string; the cache key sorts regardless,
    /// so two requests differing only in build order share a cache entry.
    #[test]
    fn cache_key_ignores_insertion_order() {
        let forward = params(&[("a", "1".into()), ("b", "2".into())]);
        let reverse = params(&[("b", "2".into()), ("a", "1".into())]);
        assert_ne!(forward.iter().next(), reverse.iter().next());
        assert_eq!(build_cache_key("/x", &forward), build_cache_key("/x", &reverse));
    }

    /// dict.update semantics: replacing a value keeps the key where it was.
    #[test]
    fn reinserting_a_key_keeps_its_position() {
        let mut built = params(&[("radiuses", "5.0".into()), ("steps", "true".into())]);
        built.insert("radiuses", "12.0");
        let keys: Vec<_> = built.iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(keys, ["radiuses", "steps"]);
        assert_eq!(built["radiuses"], ParamValue::Str("12.0".into()));
    }

    #[test]
    fn key_is_endpoint_prefix_plus_digest() {
        let key = build_cache_key("/route/v1/driving/0,0;1,1", &Params::new());
        let (endpoint, digest) = key.rsplit_once(':').expect("key has a digest suffix");
        assert_eq!(endpoint, "/route/v1/driving/0,0;1,1");
        assert_eq!(digest.len(), 64);
    }

    /// Cross-language proof: these digests were produced by Python's own
    /// `build_cache_key` (src/app/services/cache.py) for the same endpoint and
    /// params. If this test fails, the two implementations have silently split
    /// their shared Redis L2 into separate namespaces.
    ///
    /// Regenerate with:
    ///   uv run python -c "import sys; sys.path.insert(0,'src'); \
    ///     from app.services.cache import build_cache_key; \
    ///     print(build_cache_key(endpoint, params))"
    #[test]
    fn keys_match_python_reference_digests() {
        // A typical /route call, all string params.
        let route = params(&[
            ("overview", "full".into()),
            ("geometries", "geojson".into()),
            ("steps", "true".into()),
            ("annotations", "distance,duration".into()),
            ("alternatives", "false".into()),
            ("waypoints", "0;1".into()),
        ]);
        assert_eq!(
            build_cache_key("/route/v1/driving/-84.09,9.93;-84.08,9.94", &route),
            "/route/v1/driving/-84.09,9.93;-84.08,9.94:6b61930b8297e39bb8a9de43b194ef25eb42a26c00ebcb8501264326ff8e9005"
        );

        // /nearest sends `number` as an int, not a string -- the spike got this
        // wrong by stringifying every param, which alone breaks key sharing.
        let nearest = params(&[("number", ParamValue::Int(3))]);
        assert_eq!(
            build_cache_key("/nearest/v1/driving/-84.09,9.93", &nearest),
            "/nearest/v1/driving/-84.09,9.93:339e14c131e503ba931d9d24febebbe7a18d5539691e44bc6f5c8884d3a964ab"
        );

        // Floats exercise Python's repr: 5.0 must not serialise as `5`.
        let matrix = params(&[
            ("annotations", "duration,distance".into()),
            ("fallback_speed", ParamValue::Float(5.0)),
            ("scale_factor", ParamValue::Float(1.5)),
        ]);
        assert_eq!(
            build_cache_key("/table/v1/driving/-84.09,9.93;-84.08,9.94", &matrix),
            "/table/v1/driving/-84.09,9.93;-84.08,9.94:b8678c3753bede00d31299e59ff322705db5cc0b0c72e3e81528acb05d156e9e"
        );

        // The common-option serialisations, in their exact wire form.
        let common = params(&[
            ("bearings", "90,30;".into()),
            ("radiuses", "50.0;unlimited".into()),
            ("exclude", "motorway,toll".into()),
            ("skip_waypoints", "true".into()),
        ]);
        assert_eq!(
            build_cache_key("/route/v1/driving/0,0;1,1", &common),
            "/route/v1/driving/0,0;1,1:40ef220235c8894939f217a6cbf68089652c5351307f0a1e18f50afdafd5b19a"
        );
    }

    /// Different params must not collide; identical params must be stable.
    #[test]
    fn key_is_deterministic_and_param_sensitive() {
        let a = params(&[("steps", "true".into())]);
        let b = params(&[("steps", "false".into())]);
        assert_eq!(build_cache_key("/route", &a), build_cache_key("/route", &a));
        assert_ne!(build_cache_key("/route", &a), build_cache_key("/route", &b));
    }
}
