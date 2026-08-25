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
    /// No route matched. Starlette answers `{"detail":"Not Found"}`; axum's
    /// default fallback sends an empty body, so the tile handler raises this
    /// explicitly for a path FastAPI's route pattern would not have matched.
    NotFound,
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
    // `body.get("code", "Error")` substitutes only when the key is absent, so a
    // numeric or null `code` from a malformed engine reached the caller intact.
    // Coercing every non-string to "Error" hid what the engine actually said.
    json!({
        "code": map.get("code").cloned().unwrap_or_else(|| json!("Error")),
        "message": map.get("message").cloned()
            .unwrap_or_else(|| json!("Routing service error")),
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
            // A capacity refusal, like the matrix cell budget: the caller sent
            // more than the engine can be asked for, and the message says so
            // rather than surfacing the engine's 414 or a reset connection.
            ApiError::Upstream(OsrmError::RequestTooLong { bytes, limit }) => (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "detail": [ValidationError::new_public("value_error", format!(
                    "Value error, Request needs a {bytes}-byte upstream URL, over the \
                     {limit}-byte limit; send fewer coordinates or split the request"))] })),
            ).into_response(),
            ApiError::Upstream(OsrmError::Unavailable(_)) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "detail": "Internal server error" })),
            ).into_response(),
            ApiError::NotFound => (
                StatusCode::NOT_FOUND,
                Json(json!({ "detail": "Not Found" })),
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
///
/// Two things matter to callers, and serde supplies neither on its own. `loc`
/// must name the offending field, because that is what a client branches on --
/// `serde_path_to_error` provides the path. And the message must not carry
/// `at line 1 column 44`: a byte offset into the request is parser detail, and
/// pydantic emits nothing like it.
///
/// The full pydantic taxonomy is still not reproduced: no `url`, no echoed
/// `input`, and the `type` slugs are approximations. The two fields a client
/// actually reads now match.
pub fn parse_body<T: for<'de> serde::Deserialize<'de>>(body: &[u8]) -> Result<T, ApiError> {
    let deserializer = &mut serde_json::Deserializer::from_slice(body);
    serde_path_to_error::deserialize(deserializer).map_err(|err| {
        let parsed = classify(&err.inner().to_string());
        ApiError::Validation(vec![ValidationError {
            kind: parsed.kind,
            loc: location(&err.path().to_string(), parsed.field.as_deref()),
            msg: parsed.message,
        }])
    })
}

/// One serde failure, translated.
struct ParseFailure {
    kind: String,
    message: String,
    /// Set when the failure names a field serde could not attribute to a path,
    /// i.e. a missing key, which is reported against its containing object.
    field: Option<String>,
}

/// Build pydantic's `loc`: `body`, then the path to the failing field.
fn location(path: &str, missing_field: Option<&str>) -> Vec<Value> {
    use crate::models::loc_part;

    let mut loc = vec![loc_part("body")];
    if path != "." {
        loc.extend(path.split('.').filter(|part| !part.is_empty()).map(loc_part));
    }
    if let Some(field) = missing_field {
        loc.push(loc_part(field));
    }
    loc
}

/// Translate serde's wording into pydantic's for the shapes clients actually hit.
fn classify(raw: &str) -> ParseFailure {
    // Drop serde's position suffix before anything else.
    let text = raw.split(" at line ").next().unwrap_or(raw).trim();

    // `models::lax` emits `type|message`, which is how a pydantic error slug
    // survives the trip through `serde` and `serde_path_to_error`.
    if let Some((kind, message)) = text.split_once('|') {
        if !kind.is_empty() && kind.chars().all(|c| c.is_ascii_lowercase() || c == '_') {
            return ParseFailure {
                kind: kind.to_string(),
                message: message.to_string(),
                field: None,
            };
        }
    }

    if let Some(field) = text.strip_prefix("missing field ") {
        return ParseFailure {
            kind: "missing".to_string(),
            message: "Field required".to_string(),
            field: Some(field.trim().trim_matches('`').to_string()),
        };
    }
    if text.starts_with("unknown variant ") {
        if let Some(expected) = text.split("expected one of ").nth(1) {
            return ParseFailure {
                kind: "literal_error".to_string(),
                message: format!("Input should be {}", oxford(expected)),
                field: None,
            };
        }
    }
    if text.starts_with("invalid type: string") {
        return ParseFailure {
            kind: "float_parsing".to_string(),
            message: "Input should be a valid number, unable to parse string as a number"
                .to_string(),
            field: None,
        };
    }
    ParseFailure { kind: "model_attributes_type".to_string(), message: text.to_string(), field: None }
}

/// Render serde's backtick-quoted alternatives as pydantic renders them:
/// `'a', 'b' or 'c'`.
fn oxford(expected: &str) -> String {
    let options: Vec<String> = expected
        .split(',')
        .map(|option| format!("'{}'", option.trim().trim_matches('`')))
        .collect();
    match options.split_last() {
        Some((last, [])) => last.clone(),
        Some((last, head)) => format!("{} or {last}", head.join(", ")),
        None => String::new(),
    }
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
