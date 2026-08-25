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
/// How many parse failures to gather before giving up on the rest.
///
/// pydantic reports every one. Each additional failure here costs another walk
/// of the document, so a body engineered to be wrong in thousands of places
/// would otherwise buy an attacker unbounded work for one request.
const MAX_COLLECTED_ERRORS: usize = 20;

/// Bodies above this are reported one failure at a time.
///
/// The collection loop re-deserialises the whole document per failure, which is
/// cheap for an ordinary request and not for a 2,000-stop solve.
const MAX_COLLECTING_BODY: usize = 64 * 1024;

/// Placeholders tried when patching past a failure, cheapest guess first.
const PLACEHOLDERS: [fn() -> Value; 6] =
    [|| json!(0), || json!(""), || json!(false), || json!(0.0), || json!([]), || json!({})];

/// A decoded body, with any failures found getting there.
pub struct Parsed<T> {
    /// Present when the document could be made to parse, with placeholders
    /// substituted at `patched`. Absent when it could not.
    pub value: Option<T>,
    pub errors: Vec<ValidationError>,
    /// Positions holding a placeholder rather than the caller's value. Constraint
    /// errors reported against these are artefacts of the patch, not the request.
    pub patched: Vec<Vec<Value>>,
    /// The caller's document, for filling `input`.
    pub document: Value,
}

/// Decode a body, gathering every failure rather than stopping at the first.
///
/// The typed value comes back even when it failed, patched past each problem,
/// so the caller can run its own validation in the same pass -- which is what
/// pydantic does, and why a body wrong in two different ways reported one error
/// here and two there.
pub fn parse_collecting<T: for<'de> serde::Deserialize<'de>>(body: &[u8])
    -> Result<Parsed<T>, ApiError> {
    let document: Value = match serde_json::from_slice(body) {
        Ok(value) => value,
        Err(err) => {
            let parsed = classify(&err.to_string());
            return Err(ApiError::Validation(vec![ValidationError {
                kind: parsed.kind,
                loc: location(".", parsed.field.as_deref()),
                msg: parsed.message,
                input: Value::Null,
                ctx: parsed.ctx,
            }]));
        }
    };

    let mut working = document.clone();
    let mut errors: Vec<ValidationError> = Vec::new();
    let mut patched: Vec<Vec<Value>> = Vec::new();
    let collecting = body.len() <= MAX_COLLECTING_BODY;

    let value = loop {
        match deserialize_from::<T>(&working) {
            Ok(value) => break Some(value),
            Err(error) => {
                let path: Vec<Value> = error.loc.iter().skip(1).cloned().collect();
                let repeated = errors.last().is_some_and(|last| last.loc == error.loc);
                // A placeholder object has none of the fields its type wants, so
                // patching one produces "missing" errors underneath it. Those
                // describe the patch, not the request.
                let from_patch = patched.iter().any(|prefix| path.starts_with(prefix));
                if !from_patch {
                    errors.push(error);
                }
                if !collecting || repeated || path.is_empty()
                    || errors.len() >= MAX_COLLECTED_ERRORS {
                    break None;
                }
                let failure = errors.last().cloned().unwrap_or_else(|| ValidationError {
                    kind: String::new(), loc: Vec::new(), msg: String::new(),
                    input: Value::Null, ctx: None });
                if !patch_past::<T>(&mut working, &path, &failure) {
                    break None;
                }
                patched.push(path);
            }
        }
    };
    Ok(Parsed { value, errors, patched, document })
}

pub fn parse_body<T: for<'de> serde::Deserialize<'de>>(body: &[u8]) -> Result<T, ApiError> {
    let parsed = parse_collecting::<T>(body)?;
    match parsed.value {
        Some(value) if parsed.errors.is_empty() => Ok(value),
        _ => {
            let mut errors = parsed.errors;
            crate::models::fill_inputs(&mut errors, &parsed.document);
            Err(ApiError::Validation(errors))
        }
    }
}

/// One typed deserialisation of an already-parsed document.
///
/// `ValidationError` grew past clippy's threshold when it took on `input` and
/// `ctx`. Boxing it would trade a move on the failure path for an allocation,
/// and the success type here is a request struct of comparable size, so the
/// `Result` is no smaller either way.
#[allow(clippy::result_large_err, reason = "the Ok type is a request struct of similar size")]
fn deserialize_from<T: for<'de> serde::Deserialize<'de>>(document: &Value)
    -> Result<T, ValidationError> {
    serde_path_to_error::deserialize(document).map_err(|err| {
        let parsed = classify(&err.inner().to_string());
        ValidationError {
            kind: parsed.kind,
            loc: location(&err.path().to_string(), parsed.field.as_deref()),
            msg: parsed.message,
            input: Value::Null,
            ctx: parsed.ctx,
        }
    })
}

/// Substitute something at `path` the field will accept, so parsing continues.
///
/// The type wanted there is not known from the failure alone, so the candidates
/// are tried in turn and the first that moves the failure elsewhere wins. The
/// patched document is a throwaway: `input` is filled from the caller's own.
fn patch_past<T: for<'de> serde::Deserialize<'de>>(document: &mut Value, path: &[Value],
                                                   failure: &ValidationError) -> bool {
    let original = value_at(document, path).cloned();
    let preferred = preferred_placeholder(failure);
    let candidates = preferred.into_iter().chain(PLACEHOLDERS.iter().map(|make| make()));
    for placeholder in candidates {
        if !write_at(document, path, placeholder) {
            return false;
        }
        let stuck = match deserialize_from::<T>(document) {
            Ok(_) => false,
            Err(next) => next.loc.iter().skip(1).cloned().collect::<Vec<_>>() == path,
        };
        if !stuck {
            return true;
        }
    }
    if let Some(original) = original {
        write_at(document, path, original);
    }
    false
}

/// A placeholder the failing field will actually accept, where the failure says.
///
/// No generic candidate is ever a valid enum variant, so a bad `profile` could
/// not be patched past and everything behind it stayed hidden. The message
/// names the accepted values; the first one is as good as any.
fn preferred_placeholder(failure: &ValidationError) -> Option<Value> {
    match failure.kind.as_str() {
        "literal_error" => {
            let (_, rest) = failure.msg.split_once('\'')?;
            let (first, _) = rest.split_once('\'')?;
            Some(Value::String(first.to_string()))
        }
        kind if kind.starts_with("float") => Some(json!(0.0)),
        kind if kind.starts_with("int") => Some(json!(0)),
        kind if kind.starts_with("bool") => Some(json!(false)),
        "string_type" => Some(json!("")),
        _ => None,
    }
}

/// Borrow the value a `loc` path points at.
fn value_at<'a>(document: &'a Value, path: &[Value]) -> Option<&'a Value> {
    let mut cursor = document;
    for segment in path {
        cursor = match segment {
            Value::String(field) => cursor.get(field.as_str())?,
            Value::Number(index) => cursor.get(index.as_u64()? as usize)?,
            _ => return None,
        };
    }
    Some(cursor)
}

/// Write a value at a `loc` path, creating the final key if it is missing.
fn write_at(document: &mut Value, path: &[Value], value: Value) -> bool {
    let Some((last, parents)) = path.split_last() else {
        return false;
    };
    let mut cursor = document;
    for segment in parents {
        cursor = match segment {
            Value::String(field) => match cursor.get_mut(field.as_str()) {
                Some(next) => next,
                None => return false,
            },
            Value::Number(index) => match index.as_u64().and_then(|i| cursor.get_mut(i as usize)) {
                Some(next) => next,
                None => return false,
            },
            _ => return false,
        };
    }
    match (last, cursor) {
        (Value::String(field), Value::Object(map)) => {
            map.insert(field.clone(), value);
            true
        }
        (Value::Number(index), Value::Array(items)) => match index.as_u64() {
            Some(i) if (i as usize) < items.len() => {
                items[i as usize] = value;
                true
            }
            _ => false,
        },
        _ => false,
    }
}

/// One serde failure, translated.
struct ParseFailure {
    kind: String,
    message: String,
    /// The constraint context, when the failure names one. Only `literal_error`
    /// carries it today: pydantic reports the accepted values there.
    ctx: Option<Value>,
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
            ctx: None,
                field: None,
            };
        }
    }

    if let Some(field) = text.strip_prefix("missing field ") {
        return ParseFailure {
            kind: "missing".to_string(),
            message: "Field required".to_string(),
            ctx: None,
            field: Some(field.trim().trim_matches('`').to_string()),
        };
    }
    if text.starts_with("unknown variant ") {
        if let Some(expected) = text.split("expected one of ").nth(1) {
            return ParseFailure {
                kind: "literal_error".to_string(),
                message: format!("Input should be {}", oxford(expected)),
                ctx: Some(json!({ "expected": oxford(expected) })),
                field: None,
            };
        }
    }
    if text.starts_with("invalid type: string") {
        return ParseFailure {
            kind: "float_parsing".to_string(),
            message: "Input should be a valid number, unable to parse string as a number"
                .to_string(),
            ctx: None,
            field: None,
        };
    }
    ParseFailure { kind: "model_attributes_type".to_string(), message: text.to_string(),
                   ctx: None, field: None }
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
    use crate::models::RouteRequest;

    /// serde stops at the first failure; pydantic reports them all.
    #[test]
    fn every_decode_failure_is_reported_not_just_the_first() {
        let body = br#"{"origin":{"longitude":0.0,"latitude":0.0},
                        "destination":{"longitude":0.0,"latitude":0.0},
                        "profile":"nope","geometries":"nope","overview":"nope"}"#;
        let parsed = parse_collecting::<RouteRequest>(body).expect("valid JSON");
        let fields: Vec<String> = parsed.errors.iter()
            .map(|e| e.loc.last().and_then(|v| v.as_str()).unwrap_or("").to_string())
            .collect();
        assert_eq!(fields, ["profile", "geometries", "overview"],
                   "each bad literal must be reported, in field order");
    }

    /// Patching past a failure must not invent failures of its own.
    #[test]
    fn a_patched_position_does_not_produce_errors_underneath_it() {
        let body = br#"{"origin":{"longitude":0.0,"latitude":0.0}}"#;
        let parsed = parse_collecting::<RouteRequest>(body).expect("valid JSON");
        assert_eq!(parsed.errors.len(), 1, "{:?}", parsed.errors);
        assert_eq!(parsed.errors[0].kind, "missing");
        // Not `destination.longitude` and `destination.latitude` from the
        // placeholder object standing in for the absent field.
        assert_eq!(parsed.errors[0].loc.last().and_then(|v| v.as_str()), Some("destination"));
    }

    /// A body wrong in many places must not buy unbounded work.
    #[test]
    fn collection_is_bounded() {
        let fields: String = (0..200)
            .map(|i| format!(r#""f{i}":1,"#)).collect();
        let body = format!(r#"{{{fields}"profile":"nope"}}"#).into_bytes();
        let parsed = parse_collecting::<RouteRequest>(&body).expect("valid JSON");
        assert!(parsed.errors.len() <= MAX_COLLECTED_ERRORS, "{}", parsed.errors.len());
    }

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
