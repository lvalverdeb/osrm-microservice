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
pub fn document() -> String {
    ApiDoc::openapi().to_pretty_json().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc() -> serde_json::Value {
        serde_json::from_str(&document()).expect("the generated document is valid JSON")
    }

    /// Every route the gateway serves must appear, or clients generated from
    /// this document will be missing endpoints that exist.
    #[test]
    fn every_served_path_is_documented() {
        let doc = doc();
        let paths = doc["paths"].as_object().expect("paths object");
        for expected in ["/route", "/matrix", "/matrix-graph", "/match", "/trip", "/nearest",
                         "/vrp", "/vrp/allocate", "/health", "/ready", "/metrics"] {
            assert!(paths.contains_key(expected), "{expected} is missing from the document");
        }
        // The tile path is templated, so it is matched by prefix.
        assert!(paths.keys().any(|p| p.starts_with("/tile/")), "no tile path documented");
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
        let body = &doc["paths"]["/route"]["post"]["requestBody"]["content"]
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

    #[test]
    fn the_document_declares_no_authentication() {
        assert!(doc().get("security").is_none());
    }
}
