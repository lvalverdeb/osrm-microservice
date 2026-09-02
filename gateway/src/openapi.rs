//! The OpenAPI document, generated from the types that serve the requests.
//!
//! Generated rather than committed, so the document comes from the same structs that
//! deserialise requests and serialise responses, so an endpoint cannot change
//! shape without the schema following. The cost is that constraints enforced in
//! `validate()` rather than in the type -- coordinate ranges, list lengths --
//! are described by hand in the annotations below and can drift; `tests/` holds
//! the checks that catch that.

use utoipa::openapi::security::SecurityScheme;
use utoipa::{Modify, OpenApi};

use crate::handlers;
use crate::models;
use crate::vrp::solve;

#[derive(OpenApi)]
#[openapi(
    info(
        title = "OSRM API Gateway",
        description = "A gateway in front of osrm-routed, adding caching, rate \
                       limiting, request validation and vehicle-routing.",
        version = env!("CARGO_PKG_VERSION"),
    ),
    paths(
        handlers::route,
        handlers::matrix,
        handlers::matrix_graph,
        handlers::match_trace,
        handlers::trip,
        handlers::nearest,
        handlers::tile,
        handlers::vrp,
        handlers::vrp_allocate,
        handlers::health,
        handlers::ready,
        handlers::metrics,
    ),
    components(schemas(
        models::Coordinate,
        models::Stop,
        models::GpsBreadcrumb,
        models::RouteRequest,
        models::MatrixRequest,
        models::MatchRequest,
        models::TripRequest,
        models::NearestRequest,
        models::VrpRequest,
        models::ValidationError,
        solve::VehicleRoute,
        solve::VrpResponse,
        solve::VrpAllocationResponse,
    )),
    modifiers(&NoSecurity),
    tags(
        (name = "Routing", description = "Pass-throughs to osrm-routed, with caching and validation"),
        (name = "Optimisation", description = "Depot allocation and per-vehicle routing"),
        (name = "Infrastructure", description = "Probes and metrics; never rate limited"),
    )
)]
pub struct ApiDoc;

/// The gateway has no authentication of its own; it is expected to sit behind
/// something that does. Stated explicitly so the absence reads as a decision.
struct NoSecurity;

impl Modify for NoSecurity {
    fn modify(&self, openapi: &mut utoipa::openapi::OpenApi) {
        let _: Option<&SecurityScheme> = None;
        openapi.security = None;
    }
}

/// Render the document as JSON.
///
/// `vrp_max_stops` is patched in rather than annotated, because it is runtime
/// configuration: an operator can lower it on a smaller box, and a schema
/// advertising the compile-time default would then invite requests the gateway
/// rejects. FastAPI reflected the configured value too, by reading it when the
/// model class was defined.
pub fn document(vrp_max_stops: usize) -> String {
    let mut doc = match serde_json::to_value(ApiDoc::openapi()) {
        Ok(doc) => doc,
        Err(_) => return String::new(),
    };
    if let Some(stops) = doc.pointer_mut("/components/schemas/VrpRequest/properties/stops") {
        stops["maxItems"] = serde_json::json!(vrp_max_stops);
    }
    add_deprecated_aliases(&mut doc);
    serde_json::to_string_pretty(&doc).unwrap_or_default()
}

/// Document the unversioned paths too, marked deprecated. NFR-10, T-90.
///
/// The handlers declare `/v1/...`, which is the surface to integrate against.
/// The root paths still serve for the deprecation window, and a document that
/// omitted them would tell an existing integrator their working endpoint does
/// not exist -- while one that listed them as equals would give a new client no
/// reason to prefer the version that is not going away. `deprecated: true` is
/// the distinction, and it is what a generator reads to emit a warning.
///
/// Derived from the versioned entries rather than written out, so an endpoint
/// cannot be documented under one spelling and not the other.
fn add_deprecated_aliases(doc: &mut serde_json::Value) {
    let Some(paths) = doc["paths"].as_object().cloned() else {
        return;
    };
    let Some(target) = doc["paths"].as_object_mut() else {
        return;
    };
    for (path, item) in paths {
        let Some(rest) = path.strip_prefix(crate::version::PREFIX) else {
            continue;
        };
        let mut alias = item.clone();
        if let Some(operations) = alias.as_object_mut() {
            for (_, operation) in operations.iter_mut() {
                if let Some(operation) = operation.as_object_mut() {
                    operation.insert("deprecated".into(), serde_json::json!(true));
                }
            }
        }
        target.insert(rest.to_string(), alias);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The document as served, with a representative `VRP_MAX_STOPS`.
    fn doc() -> serde_json::Value {
        serde_json::from_str(&document(2000)).expect("the generated document is valid JSON")
    }

    /// Every route the gateway serves must appear, or clients generated from
    /// this document will be missing endpoints that exist.
    #[test]
    fn every_served_path_is_documented() {
        let doc = doc();
        let paths = doc["paths"].as_object().expect("paths object");
        for expected in ["/v1/route", "/v1/matrix", "/v1/matrix-graph", "/v1/match",
                         "/v1/trip", "/v1/nearest", "/v1/vrp", "/v1/vrp/allocate",
                         "/health", "/ready", "/metrics"] {
            assert!(paths.contains_key(expected), "{expected} is missing from the document");
        }
        // The tile path is templated, so it is matched by prefix.
        assert!(paths.keys().any(|p| p.starts_with("/v1/tile/")), "no tile path documented");
    }

    /// NFR-10/T-90. Both spellings are documented, and only one of them tells a
    /// generator to warn.
    #[test]
    fn the_unversioned_paths_are_documented_as_deprecated() {
        let doc = doc();
        let paths = doc["paths"].as_object().expect("paths object");
        for path in ["/route", "/matrix", "/vrp", "/vrp/allocate"] {
            let item = paths.get(path).unwrap_or_else(|| panic!("{path} undocumented"));
            assert_eq!(item["post"]["deprecated"], serde_json::json!(true), "{path}");
            assert_eq!(paths[&format!("/v1{path}")]["post"].get("deprecated"), None,
                       "/v1{path} is marked deprecated; it is the successor");
        }
    }

    /// The aliases are derived, so nothing can be served under one spelling and
    /// documented under only the other.
    #[test]
    fn every_versioned_path_has_an_alias_and_nothing_else_does() {
        let doc = doc();
        let paths = doc["paths"].as_object().expect("paths object");
        for path in paths.keys() {
            if let Some(rest) = path.strip_prefix("/v1") {
                assert!(paths.contains_key(rest), "{path} has no deprecated alias");
            }
        }
        // The probes are unversioned by design and must not have gained a twin.
        for path in ["/health", "/ready", "/metrics"] {
            assert!(!paths.contains_key(&format!("/v1{path}")),
                    "/v1{path} was documented; the probes are not versioned");
        }
    }

    #[test]
    fn request_models_are_present() {
        let doc = doc();
        let schemas = doc["components"]["schemas"].as_object().expect("schemas object");
        for model in ["RouteRequest", "MatrixRequest", "MatchRequest", "TripRequest",
                      "NearestRequest", "VrpRequest", "Coordinate", "Stop"] {
            assert!(schemas.contains_key(model), "{model} is missing from components");
        }
    }

    #[test]
    fn vrp_responses_are_described_by_their_own_types() {
        let doc = doc();
        let schemas = doc["components"]["schemas"].as_object().unwrap();
        assert!(schemas.contains_key("VrpResponse"));
        assert!(schemas.contains_key("VehicleRoute"));
        assert!(schemas.contains_key("VrpAllocationResponse"));
    }

    #[test]
    fn endpoints_reference_their_request_bodies() {
        let doc = doc();
        let body = &doc["paths"]["/v1/route"]["post"]["requestBody"]["content"]
            ["application/json"]["schema"]["$ref"];
        assert_eq!(body.as_str(), Some("#/components/schemas/RouteRequest"));
    }

    /// Every value the document advertises for an enum must be one the API
    /// actually accepts.
    ///
    /// This is the check that was missing. utoipa published the Rust variant
    /// names -- `Driving`, `TravelTime` -- while serde accepted the wire names,
    /// so the schema offered values that came straight back as 422 and any
    /// client generated from it was broken on arrival. The equivalence tests
    /// against FastAPI compared field *names* and required-lists, never the
    /// values inside them, so they passed throughout.
    #[test]
    fn advertised_enum_values_are_accepted_by_the_api() {
        let doc = doc();
        let schemas = &doc["components"]["schemas"];

        macro_rules! check {
            ($name:literal, $ty:ty) => {{
                let advertised = schemas[$name]["enum"].as_array()
                    .unwrap_or_else(|| panic!("{} has no enum in the document", $name));
                assert!(!advertised.is_empty(), "{} advertises no values", $name);
                for value in advertised {
                    let parsed: Result<$ty, _> = serde_json::from_value(value.clone());
                    assert!(parsed.is_ok(),
                            "{} advertises {value}, which the API rejects", $name);
                }
            }};
        }

        check!("Profile", crate::models::Profile);
        check!("Overview", crate::models::Overview);
        check!("Geometries", crate::models::Geometries);
        check!("ContinueStraight", crate::models::ContinueStraight);
        check!("Snapping", crate::models::Snapping);
        check!("Approach", crate::models::Approach);
        check!("MatrixAnnotation", crate::models::MatrixAnnotation);
        check!("FallbackCoordinate", crate::models::FallbackCoordinate);
        check!("TripSource", crate::models::TripSource);
        check!("TripDestination", crate::models::TripDestination);
        check!("Gaps", crate::models::Gaps);
        check!("ClusteringMode", crate::models::ClusteringMode);
    }

    /// Find a property's schema, looking through `allOf` composition.
    ///
    /// utoipa encodes a `#[serde(flatten)]` field as composition, so a request
    /// model's own properties sit inside `allOf` rather than at the top level.
    /// A naive lookup finds nothing and reports the constraint as missing --
    /// which is how this helper came to exist.
    fn property<'a>(schemas: &'a serde_json::Value, model: &str,
                    field: &str) -> &'a serde_json::Value {
        let schema = &schemas[model];
        let direct = &schema["properties"][field];
        if !direct.is_null() {
            return direct;
        }
        for part in schema["allOf"].as_array().into_iter().flatten() {
            let found = &part["properties"][field];
            if !found.is_null() {
                return found;
            }
        }
        panic!("{model} has no property {field}, directly or through allOf");
    }

    /// Every list bound the document declares is the bound `validate()`
    /// enforces -- checked by building a request at the bound and one past it.
    ///
    /// The schema previously declared no bounds at all while `validate()`
    /// enforced several, so a client generated from it produced requests the
    /// gateway rejected. Reading the numbers out of the document rather than
    /// restating them means this fails if either side moves alone.
    #[test]
    fn declared_list_bounds_are_the_enforced_ones() {
        use crate::models::Validate;

        let doc = doc();
        let schemas = &doc["components"]["schemas"];
        let bound = |model: &str, field: &str, key: &str| -> usize {
            property(schemas, model, field)[key]
                .as_u64()
                .unwrap_or_else(|| panic!("{model}.{field} declares no {key}")) as usize
        };
        // A coordinate list of `n` entries, as JSON.
        let coords = |n: usize| (0..n)
            .map(|i| format!(r#"{{"longitude":{}.0,"latitude":0.0}}"#, i % 90))
            .collect::<Vec<_>>().join(",");
        let stops = |n: usize| coords(n);

        let matrix = |n: usize| -> Vec<crate::models::ValidationError> {
            let r: crate::models::MatrixRequest =
                serde_json::from_str(&format!(r#"{{"coordinates":[{}]}}"#, coords(n))).unwrap();
            r.validate()
        };
        let min = bound("MatrixRequest", "coordinates", "minItems");
        let max = bound("MatrixRequest", "coordinates", "maxItems");
        assert!(!matrix(min - 1).is_empty(), "matrix accepts {} coordinates, below its declared minimum", min - 1);
        assert!(matrix(min).is_empty(), "matrix rejects its declared minimum of {min}");
        assert!(matrix(max).is_empty(), "matrix rejects its declared maximum of {max}");
        assert!(!matrix(max + 1).is_empty(), "matrix accepts {} coordinates, above its declared maximum", max + 1);

        let trip = |n: usize| -> Vec<crate::models::ValidationError> {
            let r: crate::models::TripRequest =
                serde_json::from_str(&format!(r#"{{"coordinates":[{}]}}"#, coords(n))).unwrap();
            r.validate()
        };
        let trip_max = bound("TripRequest", "coordinates", "maxItems");
        assert!(trip(trip_max).is_empty(), "trip rejects its declared maximum");
        assert!(!trip(trip_max + 1).is_empty(), "trip accepts more than its declared maximum");

        let crumbs = |n: usize| -> Vec<crate::models::ValidationError> {
            let list = (0..n)
                .map(|i| format!(r#"{{"longitude":0.0,"latitude":0.0,"timestamp":{i}}}"#))
                .collect::<Vec<_>>().join(",");
            let r: crate::models::MatchRequest =
                serde_json::from_str(&format!(r#"{{"breadcrumbs":[{list}]}}"#)).unwrap();
            r.validate()
        };
        let crumb_min = bound("MatchRequest", "breadcrumbs", "minItems");
        assert!(!crumbs(crumb_min - 1).is_empty(), "match accepts fewer breadcrumbs than declared");
        assert!(crumbs(crumb_min).is_empty(), "match rejects its declared minimum");

        // VrpRequest carries the runtime-configured stop ceiling, so the
        // document is rendered with a known value above and checked against it.
        let vrp = |depots: usize, stop_count: usize| -> Vec<crate::models::ValidationError> {
            let r: crate::models::VrpRequest = serde_json::from_str(&format!(
                r#"{{"depots":[{}],"stops":[{}]}}"#, coords(depots.max(1)), stops(stop_count)))
                .unwrap();
            r.validate_with(2000)
        };
        let stops_max = bound("VrpRequest", "stops", "maxItems");
        assert_eq!(stops_max, 2000, "the document should carry the configured stop ceiling");
        assert!(vrp(1, stops_max).is_empty(), "vrp rejects its declared stop maximum");
        assert!(!vrp(1, stops_max + 1).is_empty(), "vrp accepts more stops than declared");
    }

    /// The same for the numeric bounds.
    #[test]
    fn declared_numeric_bounds_are_the_enforced_ones() {
        use crate::models::Validate;

        let doc = doc();
        let schemas = &doc["components"]["schemas"];
        let lon_max = property(schemas, "Coordinate", "longitude")["maximum"]
            .as_f64().expect("longitude maximum");
        let lat_min = property(schemas, "Coordinate", "latitude")["minimum"]
            .as_f64().expect("latitude minimum");

        let nearest = |lon: f64, lat: f64, number: i64| {
            let r: crate::models::NearestRequest = serde_json::from_str(&format!(
                r#"{{"coordinate":{{"longitude":{lon},"latitude":{lat}}},"number":{number}}}"#))
                .unwrap();
            r.validate()
        };
        assert!(nearest(lon_max, 0.0, 1).is_empty(), "the declared longitude maximum is rejected");
        assert!(!nearest(lon_max + 1.0, 0.0, 1).is_empty(), "past the declared longitude maximum is accepted");
        assert!(nearest(0.0, lat_min, 1).is_empty(), "the declared latitude minimum is rejected");
        assert!(!nearest(0.0, lat_min - 1.0, 1).is_empty(), "past the declared latitude minimum is accepted");

        let number_min = property(schemas, "NearestRequest", "number")["minimum"]
            .as_i64().expect("number minimum");
        assert!(nearest(0.0, 0.0, number_min).is_empty(), "the declared number minimum is rejected");
        assert!(!nearest(0.0, 0.0, number_min - 1).is_empty(), "below the declared number minimum is accepted");
    }

    #[test]
    fn the_document_declares_no_authentication() {
        assert!(doc().get("security").is_none());
    }
}
