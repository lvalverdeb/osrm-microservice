//! Upstream query construction, mirroring `src/app/services/osrm_client.py`.
//!
//! Pure functions on purpose: the exact wire format is the part of a port most
//! likely to drift silently -- OSRM accepts many spellings and the gateway
//! passes its answer through either way -- so it is pinned by unit tests rather
//! than discovered in a differential run.
//!
//! Common options are applied *last* in every endpoint, which means a caller
//! that supplies `radiuses` to `/match` overwrites the radiuses the gateway
//! derived from breadcrumb accuracies. That collision is Python's behaviour and
//! is reproduced deliberately.

use crate::cache::{ParamValue, Params};
use crate::models::{
    CommonRoutingOptions, MatchRequest, MatrixRequest, NearestBatchRequest,
    NearestRequest, RouteRequest, TripRequest,
};
use crate::pyfloat::python_float;

/// Render a bool the way Python's `"true" if x else "false"` does.
fn flag(value: bool) -> ParamValue {
    ParamValue::Str(if value { "true" } else { "false" }.to_string())
}

/// Serialise OSRM's general options.
///
/// Each per-coordinate list joins with `;`, mapping a null entry to an empty
/// string -- except `radiuses`, where null becomes `unlimited`.
pub fn common_options(options: &CommonRoutingOptions) -> Params {
    let mut params = Params::new();
    if let Some(bearings) = &options.bearings {
        params.insert("bearings", join_optional(bearings, |v| v.clone()));
    }
    if let Some(radiuses) = &options.radiuses {
        let joined = radiuses.iter()
            .map(|v| v.map(python_float).unwrap_or_else(|| "unlimited".to_string()))
            .collect::<Vec<_>>().join(";");
        params.insert("radiuses", joined);
    }
    if let Some(hints) = &options.hints {
        params.insert("hints", join_optional(hints, |v| v.clone()));
    }
    if let Some(approaches) = &options.approaches {
        let joined = join_optional(approaches, |v| v.as_str().to_string());
        params.insert("approaches", joined);
    }
    if let Some(exclude) = &options.exclude {
        params.insert("exclude", exclude.join(","));
    }
    if let Some(snapping) = &options.snapping {
        params.insert("snapping", snapping.as_str());
    }
    if let Some(skip) = options.skip_waypoints {
        params.insert("skip_waypoints", flag(skip));
    }
    // Omitted when unset, so an untouched request builds the URL it always did.
    if let Some(generate) = options.generate_hints {
        params.insert("generate_hints", flag(generate));
    }
    params
}

/// Join a per-coordinate list with `;`, rendering nulls as empty.
fn join_optional<T>(values: &[Option<T>], render: impl Fn(&T) -> String) -> String {
    values.iter()
        .map(|v| v.as_ref().map(&render).unwrap_or_default())
        .collect::<Vec<_>>()
        .join(";")
}

/// Merge common options over already-built params, letting them win.
fn apply_common(mut params: Params, options: &CommonRoutingOptions) -> Params {
    params.extend(common_options(options));
    params
}

pub fn route(request: &RouteRequest, coordinate_count: usize) -> Params {
    let mut params = Params::new();
    params.insert("overview", request.overview.as_str());
    params.insert("geometries", request.geometries.as_str());
    params.insert("steps", flag(request.steps));
    // Every index, always: Python sends the full waypoint list rather than only
    // the intermediate ones.
    let indices = (0..coordinate_count).map(|i| i.to_string()).collect::<Vec<_>>().join(";");
    params.insert("waypoints", indices);
    params.insert("alternatives", request.alternatives.as_param());
    if let Some(annotations) = &request.annotations {
        params.insert("annotations", annotations.clone());
    }
    if let Some(continue_straight) = &request.continue_straight {
        params.insert("continue_straight", continue_straight.as_str());
    }
    apply_common(params, &request.common)
}

pub fn matrix(request: &MatrixRequest) -> Params {
    let mut params = Params::new();
    params.insert("annotations", request.annotations.as_str());
    // `if request.sources:` in Python -- an empty list is omitted, which OSRM
    // reads as "all", consistent with how the cell budget counts it.
    if let Some(sources) = non_empty(&request.sources) {
        params.insert("sources", join_indices(sources));
    }
    if let Some(destinations) = non_empty(&request.destinations) {
        params.insert("destinations", join_indices(destinations));
    }
    if let Some(speed) = request.fallback_speed {
        params.insert("fallback_speed", ParamValue::Float(speed));
    }
    if let Some(coordinate) = &request.fallback_coordinate {
        params.insert("fallback_coordinate", coordinate.as_str());
    }
    if let Some(scale) = request.scale_factor {
        params.insert("scale_factor", ParamValue::Float(scale));
    }
    apply_common(params, &request.common)
}

fn non_empty(list: &Option<Vec<i64>>) -> Option<&Vec<i64>> {
    list.as_ref().filter(|values| !values.is_empty())
}

fn join_indices(values: &[i64]) -> String {
    values.iter().map(|v| v.to_string()).collect::<Vec<_>>().join(";")
}

pub fn match_trace(request: &MatchRequest) -> Params {
    let mut params = Params::new();
    params.insert("timestamps", request.timestamps());
    params.insert("radiuses", request.radiuses());
    params.insert("overview", request.overview.as_str());
    params.insert("geometries", request.geometries.as_str());
    params.insert("steps", flag(request.steps));
    // Tri-state: null omits the parameter entirely rather than sending "false".
    if let Some(tidy) = request.tidy {
        params.insert("tidy", flag(tidy));
    }
    if let Some(annotations) = &request.annotations {
        params.insert("annotations", annotations.clone());
    }
    if let Some(gaps) = &request.gaps {
        params.insert("gaps", gaps.as_str());
    }
    if let Some(waypoints) = &request.match_waypoints {
        params.insert("waypoints", join_indices(waypoints));
    }
    apply_common(params, &request.common)
}

pub fn trip(request: &TripRequest) -> Params {
    let mut params = Params::new();
    params.insert("source", request.source.as_str());
    params.insert("destination", request.destination.as_str());
    params.insert("roundtrip", flag(request.roundtrip));
    params.insert("overview", request.overview.as_str());
    params.insert("geometries", request.geometries.as_str());
    params.insert("steps", flag(request.steps));
    if let Some(annotations) = &request.annotations {
        params.insert("annotations", annotations.clone());
    }
    apply_common(params, &request.common)
}

pub fn nearest(request: &NearestRequest) -> Params {
    let mut params = Params::new();
    // An int, not a string: the cache key hashes the native type.
    params.insert("number", ParamValue::Int(request.number));
    apply_common(params, &request.common)
}

/// The same params as `nearest`, for every coordinate in a batch.
pub fn nearest_batch(request: &NearestBatchRequest) -> Params {
    let mut params = Params::new();
    params.insert("number", ParamValue::Int(request.number));
    apply_common(params, &request.common)
}

/// Render params as a query string, stringifying values the way httpx does.
pub fn query_string(params: &Params) -> String {
    params.iter()
        .map(|(key, value)| format!("{}={}", urlencode(key), urlencode(&stringify(value))))
        .collect::<Vec<_>>()
        .join("&")
}

/// Stringify one value as httpx's `primitive_value_to_str` would.
fn stringify(value: &ParamValue) -> String {
    match value {
        ParamValue::Str(s) => s.clone(),
        ParamValue::Int(i) => i.to_string(),
        ParamValue::Float(f) => python_float(*f),
        ParamValue::Bool(b) => if *b { "true" } else { "false" }.to_string(),
        ParamValue::Null => String::new(),
    }
}

/// Percent-encode everything outside RFC 3986's unreserved set.
fn urlencode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            // httpx serialises query params with `urlencode`, whose default
            // `quote_via=quote_plus` writes a space as `+`, not `%20`. Only
            // reachable through a caller-supplied `hints`, `exclude` or
            // `bearings` value, but it changes the upstream URL and so anything
            // keyed on it, including a replay fixture.
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse<T: for<'de> serde::Deserialize<'de>>(json: &str) -> T {
        serde_json::from_str(json).expect("valid fixture")
    }

    fn text(params: &Params, key: &str) -> String {
        stringify(params.get(key).unwrap_or_else(|| panic!("missing param {key}")))
    }

    const TWO_POINTS: &str = r#""origin":{"longitude":0.0,"latitude":0.0},
                               "destination":{"longitude":1.0,"latitude":1.0}"#;

    /// Pins the exact serialisations asserted by tests/test_phase3_common.py.
    #[test]
    fn common_options_match_the_python_wire_format() {
        let request: RouteRequest = parse(&format!(r#"{{{TWO_POINTS},
            "bearings":["90,30",null],
            "radiuses":[50.0,null],
            "exclude":["motorway","toll"],
            "skip_waypoints":true}}"#));
        let params = route(&request, 2);
        assert_eq!(text(&params, "bearings"), "90,30;");
        assert_eq!(text(&params, "radiuses"), "50.0;unlimited");
        assert_eq!(text(&params, "exclude"), "motorway,toll");
        assert_eq!(text(&params, "skip_waypoints"), "true");
    }

    #[test]
    fn route_defaults_match_the_parity_baseline() {
        let request: RouteRequest = parse(&format!(r#"{{{TWO_POINTS}}}"#));
        let params = route(&request, 2);
        assert_eq!(text(&params, "overview"), "full");
        assert_eq!(text(&params, "geometries"), "geojson");
        assert_eq!(text(&params, "steps"), "true");
        assert_eq!(text(&params, "annotations"), "distance,duration");
        assert_eq!(text(&params, "alternatives"), "false");
        assert_eq!(text(&params, "waypoints"), "0;1");
    }

    /// Absent unless asked for, so an untouched request builds the URL it
    /// always did -- and every cache key derived from it stays valid.
    #[test]
    fn generate_hints_is_only_sent_when_the_caller_sets_it() {
        let plain: RouteRequest = parse(&format!("{{{TWO_POINTS}}}"));
        assert!(!route(&plain, 2).contains_key("generate_hints"));

        let off: RouteRequest = parse(&format!(r#"{{{TWO_POINTS},"generate_hints":false}}"#));
        assert_eq!(text(&route(&off, 2), "generate_hints"), "false");

        let on: RouteRequest = parse(&format!(r#"{{{TWO_POINTS},"generate_hints":true}}"#));
        assert_eq!(text(&route(&on, 2), "generate_hints"), "true");
    }

    /// A general option: every service that takes one must carry it.
    #[test]
    fn generate_hints_reaches_every_service() {
        let matrix_request: MatrixRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}],"generate_hints":false}"#);
        assert_eq!(text(&matrix(&matrix_request), "generate_hints"), "false");

        let trip_request: TripRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}],"generate_hints":false}"#);
        assert_eq!(text(&trip(&trip_request), "generate_hints"), "false");

        let nearest_request: NearestRequest = parse(
            r#"{"coordinate":{"longitude":0.0,"latitude":0.0},"generate_hints":false}"#);
        assert_eq!(text(&nearest(&nearest_request), "generate_hints"), "false");
    }

    #[test]
    fn route_sends_every_waypoint_index() {
        let request: RouteRequest = parse(&format!(r#"{{{TWO_POINTS},
            "waypoints":[{{"longitude":0.5,"latitude":0.5}}]}}"#));
        assert_eq!(text(&route(&request, 3), "waypoints"), "0;1;2");
    }

    #[test]
    fn matrix_omits_empty_index_lists() {
        let request: MatrixRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}],
                "sources":[],"destinations":[1]}"#);
        let params = matrix(&request);
        assert!(!params.contains_key("sources"));
        assert_eq!(text(&params, "destinations"), "1");
    }

    #[test]
    fn matrix_keeps_floats_native_for_the_cache_key() {
        let request: MatrixRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}],
                "fallback_speed":5.0}"#);
        let params = matrix(&request);
        assert_eq!(params["fallback_speed"], ParamValue::Float(5.0));
        assert_eq!(text(&params, "fallback_speed"), "5.0");
    }

    #[test]
    fn nearest_number_stays_an_integer() {
        let request: NearestRequest = parse(
            r#"{"coordinate":{"longitude":0.0,"latitude":0.0},"number":3}"#);
        assert_eq!(nearest(&request)["number"], ParamValue::Int(3));
    }

    /// Null omits `tidy` rather than sending "false" -- a three-way flag.
    #[test]
    fn match_tidy_is_tri_state() {
        let crumbs = r#""breadcrumbs":[{"longitude":0.0,"latitude":0.0,"timestamp":1},
                                       {"longitude":1.0,"latitude":1.0,"timestamp":2}]"#;
        let unset: MatchRequest = parse(&format!("{{{crumbs}}}"));
        assert!(!match_trace(&unset).contains_key("tidy"));

        let off: MatchRequest = parse(&format!("{{{crumbs},\"tidy\":false}}"));
        assert_eq!(text(&match_trace(&off), "tidy"), "false");
    }

    /// Reproduced from Python: the caller's radiuses overwrite the ones derived
    /// from breadcrumb accuracies, because common options are applied last.
    #[test]
    fn caller_radiuses_overwrite_breadcrumb_derived_ones() {
        let request: MatchRequest = parse(
            r#"{"breadcrumbs":[{"longitude":0.0,"latitude":0.0,"timestamp":1},
                               {"longitude":1.0,"latitude":1.0,"timestamp":2}],
                "radiuses":[12.0,null]}"#);
        assert_eq!(text(&match_trace(&request), "radiuses"), "12.0;unlimited");
    }

    #[test]
    fn match_without_caller_radiuses_uses_breadcrumb_accuracies() {
        let request: MatchRequest = parse(
            r#"{"breadcrumbs":[{"longitude":0.0,"latitude":0.0,"timestamp":1,"accuracy_meters":8.0},
                               {"longitude":1.0,"latitude":1.0,"timestamp":2}]}"#);
        let params = match_trace(&request);
        assert_eq!(text(&params, "radiuses"), "8.0;5.0");
        assert_eq!(text(&params, "timestamps"), "1;2");
        assert_eq!(text(&params, "steps"), "false");
    }

    #[test]
    fn trip_defaults_match_the_parity_baseline() {
        let request: TripRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}]}"#);
        let params = trip(&request);
        assert_eq!(text(&params, "source"), "first");
        assert_eq!(text(&params, "destination"), "last");
        assert_eq!(text(&params, "roundtrip"), "true");
        assert_eq!(text(&params, "steps"), "true");
    }

    #[test]
    fn query_string_percent_encodes_separators() {
        let mut params = Params::new();
        params.insert("radiuses", "50.0;unlimited");
        assert_eq!(query_string(&params), "radiuses=50.0%3Bunlimited");
    }
}
