//! Error responses, matching what FastAPI and the Python handlers produce.
//!
//! `detail` is polymorphic there and must stay so here: an object for a parsed
//! upstream error, a bare string when that body could not be parsed or when
//! something unexpected failed, and a list for validation errors.

use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

use crate::models::ValidationError;
use crate::osrm::client::OsrmError;

/// Everything a handler can fail with.
#[derive(Debug)]
pub enum ApiError {
    /// The request did not satisfy the schema. 422, as pydantic produces.
    Validation(Vec<ValidationError>),
    /// The engine answered with an error, or could not be reached.
    Upstream(OsrmError),
    /// The optimisation admission gate shed this request.
    CapacityExhausted { retry_after: u64 },
}

impl From<OsrmError> for ApiError {
    fn from(error: OsrmError) -> Self {
        ApiError::Upstream(error)
    }
}

/// Extract structured detail from an upstream error body.
///
/// Mirrors `_parse_osrm_error`: an unparseable body yields the bare string
/// rather than an object, which is why callers cannot assume `detail` is a dict.
fn upstream_detail(body: &Option<Value>) -> Value {
    let Some(Value::Object(map)) = body else {
        return Value::String("Routing service error".to_string());
    };
    json!({
        "code": map.get("code").and_then(Value::as_str).unwrap_or("Error"),
        "message": map.get("message").and_then(Value::as_str).unwrap_or("Routing service error"),
    })
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        match self {
            ApiError::Validation(errors) => (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "detail": errors })),
            ).into_response(),
            ApiError::Upstream(OsrmError::Status { status, body }) => {
                let code = StatusCode::from_u16(status)
                    .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
                (code, Json(json!({ "detail": upstream_detail(&body) }))).into_response()
            }
            // Transport failures and exhausted retries both land here, which is
            // why a persistently 503-ing engine surfaces as 500 rather than 503.
            ApiError::Upstream(OsrmError::Unavailable(_)) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "detail": "Internal server error" })),
            ).into_response(),
            ApiError::CapacityExhausted { retry_after } => (
                StatusCode::SERVICE_UNAVAILABLE,
                [(header::RETRY_AFTER, retry_after.to_string())],
                Json(json!({ "detail": "Optimization capacity exhausted, retry shortly" })),
            ).into_response(),
        }
    }
}

/// Decode a request body, reporting a parse failure the way pydantic would.
pub fn parse_body<T: for<'de> serde::Deserialize<'de>>(body: &[u8]) -> Result<T, ApiError> {
    serde_json::from_slice(body).map_err(|err| {
        ApiError::Validation(vec![ValidationError {
            kind: "model_attributes_type".to_string(),
            loc: vec!["body".to_string()],
            msg: err.to_string(),
        }])
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parsed_upstream_body_becomes_an_object() {
        let body = Some(json!({"code": "NoRoute", "message": "Impossible route"}));
        assert_eq!(upstream_detail(&body), json!({"code": "NoRoute", "message": "Impossible route"}));
    }

    #[test]
    fn missing_fields_fall_back_to_python_defaults() {
        let body = Some(json!({}));
        assert_eq!(upstream_detail(&body),
                   json!({"code": "Error", "message": "Routing service error"}));
    }

    /// The polymorphism a port must preserve: an unparseable body yields a bare
    /// string, so `detail` is not always an object.
    #[test]
    fn unparseable_body_becomes_a_bare_string() {
        assert_eq!(upstream_detail(&None), json!("Routing service error"));
    }

    #[test]
    fn a_non_object_body_also_becomes_a_bare_string() {
        assert_eq!(upstream_detail(&Some(json!("nonsense"))), json!("Routing service error"));
    }
}
