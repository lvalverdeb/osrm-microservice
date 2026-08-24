//! Request models, mirroring `src/app/models/schemas.py`.
//!
//! Two rules shaped this module.
//!
//! **Match pydantic's permissiveness, not its docstrings.** The per-coordinate
//! options (`bearings`, `radiuses`, `hints`, `approaches`) are documented as
//! needing one entry per coordinate, but nothing enforces that: a mismatched
//! list is forwarded to OSRM, which answers 400, and the gateway passes that
//! through. `bearings` entries are free-form strings whose `"angle,deviation"`
//! shape is never parsed. Typing them more strictly here would reject requests
//! the Python gateway accepts, which is a behaviour change wearing the costume
//! of a bug fix.
//!
//! **Defaults differ per endpoint and the differences are deliberate.**
//! `/match` defaults `steps` to false and `annotations` to null where `/route`
//! and `/trip` default them to true and `"distance,duration"`.

use serde::Deserialize;
use utoipa::ToSchema;

use crate::pyfloat::python_float;

/// One validation failure, shaped like a pydantic error entry.
#[derive(Debug, Clone, serde::Serialize, ToSchema)]
pub struct ValidationError {
    /// Pydantic's error type slug, e.g. `greater_than_equal`.
    #[serde(rename = "type")]
    pub kind: String,
    /// Path to the offending field, always starting with `body`.
    pub loc: Vec<String>,
    pub msg: String,
}

impl ValidationError {
    fn new(kind: &str, loc: &[&str], msg: String) -> Self {
        let mut path = vec!["body".to_string()];
        path.extend(loc.iter().map(|s| s.to_string()));
        Self { kind: kind.to_string(), loc: path, msg }
    }
}

/// Anything that can reject itself before an upstream call is made.
pub trait Validate {
    /// Return every constraint this value violates.
    fn validate(&self) -> Vec<ValidationError>;
}

macro_rules! literal_enum {
    ($name:ident { $( $variant:ident => $wire:literal ),* $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, ToSchema)]
        pub enum $name {
            $( #[serde(rename = $wire)] $variant, )*
        }

        impl $name {
            /// The exact string sent upstream.
            pub fn as_str(&self) -> &'static str {
                match self { $( Self::$variant => $wire, )* }
            }
        }
    };
}

literal_enum!(Profile { Driving => "driving", Cycling => "cycling", Walking => "walking" });
literal_enum!(Overview { Simplified => "simplified", Full => "full", False => "false" });
literal_enum!(Geometries {
    Polyline => "polyline", Polyline6 => "polyline6", Geojson => "geojson"
});
literal_enum!(ContinueStraight {
    Default => "default", True => "true", False => "false"
});
literal_enum!(Snapping { Default => "default", Any => "any" });
literal_enum!(Approach { Unrestricted => "unrestricted", Curb => "curb" });
literal_enum!(MatrixAnnotation {
    Duration => "duration", Distance => "distance", Both => "duration,distance"
});
literal_enum!(FallbackCoordinate { Input => "input", Snapped => "snapped" });
literal_enum!(TripSource { First => "first", Any => "any" });
literal_enum!(TripDestination { Last => "last", Any => "any" });
literal_enum!(Gaps { Split => "split", Ignore => "ignore" });

fn driving() -> Profile { Profile::Driving }
fn full() -> Overview { Overview::Full }
fn geojson() -> Geometries { Geometries::Geojson }
fn yes() -> bool { true }
fn distance_duration() -> Option<String> { Some("distance,duration".to_string()) }
fn one() -> i64 { 1 }
fn default_capacity() -> i64 { 35 }
fn hysteresis() -> f64 { 2000.0 }

/// A longitude/latitude pair.
#[derive(Debug, Clone, Copy, Deserialize, serde::Serialize, ToSchema)]
pub struct Coordinate {
    pub longitude: f64,
    pub latitude: f64,
}

impl Coordinate {
    /// Render as OSRM's `lon,lat`, spelled the way Python spells it.
    pub fn as_pair(&self) -> String {
        format!("{},{}", python_float(self.longitude), python_float(self.latitude))
    }

    fn validate_at(&self, loc: &[&str]) -> Vec<ValidationError> {
        let mut errors = Vec::new();
        // Bounds are rendered as integers because the pydantic schema declares
        // them as integers -- `Field(..., ge=-180, le=180)` -- and pydantic
        // echoes the constraint value as given. Rendering them as floats
        // produced "less than or equal to 180.0" against Python's "180", which
        // an example client printing `Detail:` surfaced directly.
        let mut check = |value: f64, field: &str, min: i64, max: i64| {
            let mut path = loc.to_vec();
            path.push(field);
            if value < min as f64 {
                errors.push(ValidationError::new("greater_than_equal", &path,
                    format!("Input should be greater than or equal to {min}")));
            } else if value > max as f64 {
                errors.push(ValidationError::new("less_than_equal", &path,
                    format!("Input should be less than or equal to {max}")));
            }
        };
        check(self.longitude, "longitude", -180, 180);
        check(self.latitude, "latitude", -90, 90);
        errors
    }
}

/// Join coordinates into OSRM's semicolon-separated path segment.
pub fn join_coordinates(coordinates: &[Coordinate]) -> String {
    coordinates.iter().map(Coordinate::as_pair).collect::<Vec<_>>().join(";")
}

/// Validate a list of coordinates and its length bounds.
fn validate_coordinates(coordinates: &[Coordinate], field: &str, min: usize,
                        max: usize) -> Vec<ValidationError> {
    let mut errors = length_errors(coordinates.len(), field, min, max);
    for (index, coordinate) in coordinates.iter().enumerate() {
        let index_str = index.to_string();
        errors.extend(coordinate.validate_at(&[field, &index_str]));
    }
    errors
}

/// Check a list length against pydantic's `min_length`/`max_length`.
fn length_errors(len: usize, field: &str, min: usize, max: usize) -> Vec<ValidationError> {
    if len < min {
        return vec![ValidationError::new("too_short", &[field],
            format!("List should have at least {min} items after validation, not {len}"))];
    }
    if len > max {
        return vec![ValidationError::new("too_long", &[field],
            format!("List should have at most {max} items after validation, not {len}"))];
    }
    Vec::new()
}

/// OSRM's general options, shared by every routing request.
///
/// Flattened into each request so the wire format matches Python's, where these
/// are inherited fields rather than a nested object.
#[derive(Debug, Clone, Default, Deserialize, ToSchema)]
pub struct CommonRoutingOptions {
    pub bearings: Option<Vec<Option<String>>>,
    pub radiuses: Option<Vec<Option<f64>>>,
    pub hints: Option<Vec<Option<String>>>,
    pub approaches: Option<Vec<Option<Approach>>>,
    pub exclude: Option<Vec<String>>,
    pub snapping: Option<Snapping>,
    pub skip_waypoints: Option<bool>,
}

/// `alternatives` accepts either a flag or a count, and serialises differently
/// for each: `true`/`false` for the flag, the bare number for a count.
#[derive(Debug, Clone, Copy, Deserialize, ToSchema)]
#[serde(untagged)]
pub enum Alternatives {
    Flag(bool),
    Count(i64),
}

impl Default for Alternatives {
    fn default() -> Self {
        Alternatives::Flag(false)
    }
}

impl Alternatives {
    /// Serialise as Python's three-way ternary does.
    pub fn as_param(&self) -> String {
        match self {
            Alternatives::Flag(true) => "true".to_string(),
            Alternatives::Flag(false) => "false".to_string(),
            Alternatives::Count(n) => n.to_string(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct RouteRequest {
    pub origin: Coordinate,
    pub destination: Coordinate,
    #[serde(default)]
    pub waypoints: Option<Vec<Coordinate>>,
    #[serde(default)]
    pub alternatives: Alternatives,
    #[serde(default = "driving")]
    pub profile: Profile,
    #[serde(default = "full")]
    pub overview: Overview,
    #[serde(default = "geojson")]
    pub geometries: Geometries,
    #[serde(default = "yes")]
    pub steps: bool,
    #[serde(default = "distance_duration")]
    pub annotations: Option<String>,
    #[serde(default)]
    pub continue_straight: Option<ContinueStraight>,
    #[serde(flatten)]
    pub common: CommonRoutingOptions,
}

impl RouteRequest {
    /// Origin, then any waypoints, then destination -- the order Python builds.
    pub fn coordinates(&self) -> Vec<Coordinate> {
        let mut points = vec![self.origin];
        points.extend(self.waypoints.iter().flatten().copied());
        points.push(self.destination);
        points
    }
}

impl Validate for RouteRequest {
    fn validate(&self) -> Vec<ValidationError> {
        let mut errors = self.origin.validate_at(&["origin"]);
        errors.extend(self.destination.validate_at(&["destination"]));
        if let Some(waypoints) = &self.waypoints {
            errors.extend(validate_coordinates(waypoints, "waypoints", 0, 200));
        }
        errors
    }
}

#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct MatrixRequest {
    pub coordinates: Vec<Coordinate>,
    #[serde(default)]
    pub sources: Option<Vec<i64>>,
    #[serde(default)]
    pub destinations: Option<Vec<i64>>,
    #[serde(default = "driving")]
    pub profile: Profile,
    #[serde(default = "MatrixRequest::default_annotations")]
    pub annotations: MatrixAnnotation,
    #[serde(default)]
    pub fallback_speed: Option<f64>,
    #[serde(default)]
    pub fallback_coordinate: Option<FallbackCoordinate>,
    #[serde(default)]
    pub scale_factor: Option<f64>,
    #[serde(flatten)]
    pub common: CommonRoutingOptions,
}

impl MatrixRequest {
    fn default_annotations() -> MatrixAnnotation {
        MatrixAnnotation::Both
    }

    /// One batch of the depot-to-stop matrix the VRP solver builds.
    ///
    /// Takes the schema defaults for everything else, which is what
    /// `_get_depot_to_stop_matrix` relies on -- notably `annotations`, since
    /// the allocation needs both durations and distances.
    pub fn vrp_batch(coordinates: Vec<Coordinate>, sources: Vec<i64>,
                     destinations: Vec<i64>) -> Self {
        Self {
            coordinates,
            sources: Some(sources),
            destinations: Some(destinations),
            profile: Profile::Driving,
            annotations: MatrixAnnotation::Both,
            fallback_speed: None,
            fallback_coordinate: None,
            scale_factor: None,
            common: CommonRoutingOptions::default(),
        }
    }

    /// Cells this request would ask the engine for.
    ///
    /// An omitted *or empty* list counts as every coordinate, because Python
    /// writes `len(self.sources or range(total))` and `[]` is falsy. Duplicate
    /// indices count individually, so the budget is on the product rather than
    /// on the distinct set.
    pub fn cell_count(&self) -> usize {
        let total = self.coordinates.len();
        let side = |list: &Option<Vec<i64>>| match list {
            Some(values) if !values.is_empty() => values.len(),
            _ => total,
        };
        side(&self.sources) * side(&self.destinations)
    }

    /// Validate against the engine's cell budget.
    pub fn validate_budget(&self, max_cells: usize) -> Vec<ValidationError> {
        let cells = self.cell_count();
        if cells <= max_cells {
            return Vec::new();
        }
        // Wording matches Python's validator exactly: the message is
        // user-visible, and tests/test_matrix_capacity.py asserts on it.
        vec![ValidationError::new("value_error", &[], format!(
            "Value error, Matrix of {cells} cells (sources x destinations) exceeds the \
             {max_cells}-cell limit; narrow it with 'sources'/'destinations' or split \
             the request"))]
    }
}

impl Validate for MatrixRequest {
    fn validate(&self) -> Vec<ValidationError> {
        validate_coordinates(&self.coordinates, "coordinates", 2, 5000)
    }
}

#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct GpsBreadcrumb {
    pub longitude: f64,
    pub latitude: f64,
    pub timestamp: i64,
    /// Defaults to 5.0 rather than null, so it is always present in the
    /// `radiuses` the gateway derives.
    #[serde(default = "GpsBreadcrumb::default_accuracy")]
    pub accuracy_meters: Option<f64>,
}

impl GpsBreadcrumb {
    fn default_accuracy() -> Option<f64> {
        Some(5.0)
    }

    fn coordinate(&self) -> Coordinate {
        Coordinate { longitude: self.longitude, latitude: self.latitude }
    }

    /// Render `accuracy_meters` as Python's `str()` would.
    ///
    /// An explicit null reaches `str(None)` there and emits the literal string
    /// `"None"`. Reproduced rather than fixed: it is observable behaviour, and
    /// changing it here would make a parity diff mean two things at once.
    fn radius(&self) -> String {
        match self.accuracy_meters {
            Some(value) => python_float(value),
            None => "None".to_string(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct MatchRequest {
    pub breadcrumbs: Vec<GpsBreadcrumb>,
    #[serde(default = "driving")]
    pub profile: Profile,
    #[serde(default = "full")]
    pub overview: Overview,
    #[serde(default = "geojson")]
    pub geometries: Geometries,
    /// Defaults to false here, unlike /route and /trip.
    #[serde(default)]
    pub steps: bool,
    /// Defaults to null here, unlike /route and /trip.
    #[serde(default)]
    pub annotations: Option<String>,
    #[serde(default)]
    pub gaps: Option<Gaps>,
    #[serde(default)]
    pub tidy: Option<bool>,
    #[serde(default)]
    pub match_waypoints: Option<Vec<i64>>,
    #[serde(flatten)]
    pub common: CommonRoutingOptions,
}

impl MatchRequest {
    pub fn coordinates(&self) -> Vec<Coordinate> {
        self.breadcrumbs.iter().map(GpsBreadcrumb::coordinate).collect()
    }

    /// Semicolon-joined timestamps, one per breadcrumb.
    pub fn timestamps(&self) -> String {
        self.breadcrumbs.iter().map(|b| b.timestamp.to_string()).collect::<Vec<_>>().join(";")
    }

    /// Semicolon-joined accuracies, one per breadcrumb.
    pub fn radiuses(&self) -> String {
        self.breadcrumbs.iter().map(GpsBreadcrumb::radius).collect::<Vec<_>>().join(";")
    }
}

impl Validate for MatchRequest {
    fn validate(&self) -> Vec<ValidationError> {
        let mut errors = length_errors(self.breadcrumbs.len(), "breadcrumbs", 2, 5000);
        for (index, crumb) in self.breadcrumbs.iter().enumerate() {
            let index_str = index.to_string();
            errors.extend(crumb.coordinate().validate_at(&["breadcrumbs", &index_str]));
            if crumb.timestamp < 0 {
                errors.push(ValidationError::new("greater_than_equal",
                    &["breadcrumbs", &index_str, "timestamp"],
                    "Input should be greater than or equal to 0".to_string()));
            }
        }
        errors
    }
}

#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct TripRequest {
    pub coordinates: Vec<Coordinate>,
    #[serde(default = "yes")]
    pub roundtrip: bool,
    #[serde(default = "TripRequest::default_source")]
    pub source: TripSource,
    #[serde(default = "TripRequest::default_destination")]
    pub destination: TripDestination,
    #[serde(default = "driving")]
    pub profile: Profile,
    #[serde(default = "full")]
    pub overview: Overview,
    #[serde(default = "geojson")]
    pub geometries: Geometries,
    #[serde(default = "yes")]
    pub steps: bool,
    #[serde(default = "distance_duration")]
    pub annotations: Option<String>,
    #[serde(flatten)]
    pub common: CommonRoutingOptions,
}

impl TripRequest {
    fn default_source() -> TripSource {
        TripSource::First
    }

    fn default_destination() -> TripDestination {
        TripDestination::Last
    }

    /// The internal `/trip` call the VRP solver makes for one chunk.
    ///
    /// `destination` is `any` regardless of `roundtrip`, which is what
    /// `_solve_tsp_chunk` hardcodes. Every other field takes the schema
    /// default, and `vrp_chunk_matches_schema_defaults` pins that so the two
    /// cannot drift apart.
    pub fn vrp_chunk(coordinates: Vec<Coordinate>, roundtrip: bool) -> Self {
        Self {
            coordinates,
            roundtrip,
            source: TripSource::First,
            destination: TripDestination::Any,
            profile: Profile::Driving,
            overview: Overview::Full,
            geometries: Geometries::Geojson,
            steps: true,
            annotations: Some("distance,duration".to_string()),
            common: CommonRoutingOptions::default(),
        }
    }
}

impl Validate for TripRequest {
    fn validate(&self) -> Vec<ValidationError> {
        validate_coordinates(&self.coordinates, "coordinates", 2, 200)
    }
}

#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct NearestRequest {
    pub coordinate: Coordinate,
    /// No upper bound, matching the Python schema.
    #[serde(default = "one")]
    pub number: i64,
    #[serde(default = "driving")]
    pub profile: Profile,
    #[serde(flatten)]
    pub common: CommonRoutingOptions,
}

impl Validate for NearestRequest {
    fn validate(&self) -> Vec<ValidationError> {
        let mut errors = self.coordinate.validate_at(&["coordinate"]);
        if self.number < 1 {
            errors.push(ValidationError::new("greater_than_equal", &["number"],
                "Input should be greater than or equal to 1".to_string()));
        }
        errors
    }
}

/// A stop or depot: a coordinate that may carry a caller-supplied identifier.
#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct Stop {
    pub longitude: f64,
    pub latitude: f64,
    #[serde(default)]
    pub id: Option<serde_json::Value>,
}

impl Stop {
    pub fn coordinate(&self) -> Coordinate {
        Coordinate { longitude: self.longitude, latitude: self.latitude }
    }
}

#[derive(Debug, Clone, Deserialize, ToSchema)]
pub struct VrpRequest {
    pub depots: Vec<Stop>,
    pub stops: Vec<Stop>,
    #[serde(default = "default_capacity")]
    pub capacity: i64,
    #[serde(default)]
    pub max_radius_km: Option<f64>,
    #[serde(default = "VrpRequest::default_mode")]
    pub clustering_mode: ClusteringMode,
    #[serde(default = "hysteresis")]
    pub hysteresis_m: f64,
    #[serde(default = "yes")]
    pub roundtrip: bool,
}

literal_enum!(ClusteringMode {
    Distance => "distance", TravelTime => "travel_time", Radial => "radial"
});

impl VrpRequest {
    fn default_mode() -> ClusteringMode {
        ClusteringMode::TravelTime
    }

    /// Validate, including the import-time-configured stop ceiling.
    pub fn validate_with(&self, max_stops: usize) -> Vec<ValidationError> {
        let mut errors = length_errors(self.depots.len(), "depots", 1, 500);
        errors.extend(length_errors(self.stops.len(), "stops", 1, max_stops));
        if self.capacity <= 0 || self.capacity > 10_000 {
            errors.push(ValidationError::new("greater_than", &["capacity"],
                "Input should be greater than 0 and less than or equal to 10000".to_string()));
        }
        errors
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse<T: for<'de> Deserialize<'de>>(json: &str) -> T {
        serde_json::from_str(json).expect("valid fixture")
    }

    #[test]
    fn route_defaults_match_the_python_schema() {
        let request: RouteRequest = parse(
            r#"{"origin":{"longitude":0.0,"latitude":0.0},
                "destination":{"longitude":1.0,"latitude":1.0}}"#);
        assert_eq!(request.profile.as_str(), "driving");
        assert_eq!(request.overview.as_str(), "full");
        assert_eq!(request.geometries.as_str(), "geojson");
        assert!(request.steps);
        assert_eq!(request.annotations.as_deref(), Some("distance,duration"));
        assert_eq!(request.alternatives.as_param(), "false");
    }

    /// /match deliberately differs from /route on both of these.
    #[test]
    fn match_defaults_differ_from_route() {
        let request: MatchRequest = parse(
            r#"{"breadcrumbs":[{"longitude":0.0,"latitude":0.0,"timestamp":1},
                               {"longitude":1.0,"latitude":1.0,"timestamp":2}]}"#);
        assert!(!request.steps);
        assert_eq!(request.annotations, None);
    }

    #[test]
    fn explicit_null_annotations_beats_the_default() {
        let request: RouteRequest = parse(
            r#"{"origin":{"longitude":0.0,"latitude":0.0},
                "destination":{"longitude":1.0,"latitude":1.0},"annotations":null}"#);
        assert_eq!(request.annotations, None);
    }

    #[test]
    fn alternatives_accepts_a_flag_or_a_count() {
        let flag: Alternatives = parse("true");
        let count: Alternatives = parse("3");
        assert_eq!(flag.as_param(), "true");
        assert_eq!(count.as_param(), "3");
    }

    #[test]
    fn coordinates_are_ordered_origin_waypoints_destination() {
        let request: RouteRequest = parse(
            r#"{"origin":{"longitude":0.0,"latitude":0.0},
                "destination":{"longitude":2.0,"latitude":2.0},
                "waypoints":[{"longitude":1.0,"latitude":1.0}]}"#);
        assert_eq!(join_coordinates(&request.coordinates()), "0.0,0.0;1.0,1.0;2.0,2.0");
    }

    #[test]
    fn out_of_range_coordinates_are_rejected() {
        let request: RouteRequest = parse(
            r#"{"origin":{"longitude":-200.0,"latitude":0.0},
                "destination":{"longitude":1.0,"latitude":100.0}}"#);
        let errors = request.validate();
        assert_eq!(errors.len(), 2);
        assert_eq!(errors[0].loc, ["body", "origin", "longitude"]);
        assert_eq!(errors[1].loc, ["body", "destination", "latitude"]);
        // Pydantic echoes the schema's integer bound, not a float.
        assert_eq!(errors[0].msg, "Input should be greater than or equal to -180");
        assert_eq!(errors[1].msg, "Input should be less than or equal to 90");
    }

    /// The one real validator in the Python schema, and its falsy-list rule:
    /// an empty `sources` counts as every coordinate, not as zero.
    #[test]
    fn cell_budget_counts_empty_lists_as_all_coordinates() {
        let request: MatrixRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}],"sources":[]}"#);
        assert_eq!(request.cell_count(), 4);
    }

    #[test]
    fn cell_budget_is_the_product_not_the_coordinate_count() {
        // 4 x 2500 is accepted at 10000 cells; 101 symmetric coordinates is not.
        let coordinates = |n: usize| (0..n)
            .map(|i| format!(r#"{{"longitude":{}.0,"latitude":0.0}}"#, i % 90))
            .collect::<Vec<_>>().join(",");
        let symmetric: MatrixRequest = parse(&format!(r#"{{"coordinates":[{}]}}"#, coordinates(101)));
        assert_eq!(symmetric.cell_count(), 10_201);
        assert_eq!(symmetric.validate_budget(10_000).len(), 1);
    }

    #[test]
    fn budget_error_message_names_the_limit() {
        let request: MatrixRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}]}"#);
        let errors = request.validate_budget(2);
        assert_eq!(errors[0].msg,
            "Value error, Matrix of 4 cells (sources x destinations) exceeds the 2-cell \
             limit; narrow it with 'sources'/'destinations' or split the request");
    }

    #[test]
    fn too_few_breadcrumbs_is_rejected() {
        let request: MatchRequest = parse(
            r#"{"breadcrumbs":[{"longitude":0.0,"latitude":0.0,"timestamp":1}]}"#);
        let errors = request.validate();
        assert_eq!(errors[0].kind, "too_short");
    }

    #[test]
    fn breadcrumb_accuracy_defaults_to_five() {
        let request: MatchRequest = parse(
            r#"{"breadcrumbs":[{"longitude":0.0,"latitude":0.0,"timestamp":1},
                               {"longitude":1.0,"latitude":1.0,"timestamp":2}]}"#);
        assert_eq!(request.radiuses(), "5.0;5.0");
    }

    /// Reproduced from Python, where `str(None)` reaches the query string.
    #[test]
    fn explicit_null_accuracy_emits_the_literal_none() {
        let request: MatchRequest = parse(
            r#"{"breadcrumbs":[{"longitude":0.0,"latitude":0.0,"timestamp":1,"accuracy_meters":null},
                               {"longitude":1.0,"latitude":1.0,"timestamp":2}]}"#);
        assert_eq!(request.radiuses(), "None;5.0");
    }

    #[test]
    fn per_coordinate_options_stay_loose() {
        // A bearings list shorter than the coordinate list is accepted, as it is
        // by the Python schema; OSRM is what rejects it.
        let request: RouteRequest = parse(
            r#"{"origin":{"longitude":0.0,"latitude":0.0},
                "destination":{"longitude":1.0,"latitude":1.0},
                "bearings":["90,30",null]}"#);
        assert!(request.validate().is_empty());
        assert_eq!(request.common.bearings.as_ref().unwrap().len(), 2);
    }

    /// The schema advertises a stop ceiling; this is the check that it is
    /// actually enforced. A snapshot test can only confirm the document.
    #[test]
    fn vrp_enforces_the_stop_ceiling_it_advertises() {
        let stops: Vec<String> = (0..6)
            .map(|i| format!(r#"{{"longitude":{}.0,"latitude":0.0}}"#, i))
            .collect();
        let request: VrpRequest = parse(&format!(
            r#"{{"depots":[{{"longitude":0.0,"latitude":0.0}}],"stops":[{}]}}"#,
            stops.join(",")));

        assert!(request.validate_with(2000).is_empty(), "6 stops is well inside the ceiling");
        let errors = request.validate_with(5);
        assert_eq!(errors[0].kind, "too_long");
        assert_eq!(errors[0].loc, ["body", "stops"]);
        assert!(errors[0].msg.contains("at most 5 items"), "{}", errors[0].msg);
    }

    #[test]
    fn vrp_requires_at_least_one_depot() {
        let request: VrpRequest = parse(r#"{"depots":[],"stops":[{"longitude":0.0,"latitude":0.0}]}"#);
        assert_eq!(request.validate_with(2000)[0].loc, ["body", "depots"]);
    }

    #[test]
    fn nearest_rejects_a_number_below_one() {
        let request: NearestRequest = parse(
            r#"{"coordinate":{"longitude":0.0,"latitude":0.0},"number":0}"#);
        assert_eq!(request.validate()[0].loc, ["body", "number"]);
    }

    /// The hand-built VRP chunk request must equal what the schema would
    /// produce for the same explicit fields.
    #[test]
    fn vrp_chunk_matches_schema_defaults() {
        let points = vec![Coordinate { longitude: 0.0, latitude: 0.0 },
                          Coordinate { longitude: 1.0, latitude: 1.0 }];
        let built = TripRequest::vrp_chunk(points, true);
        let parsed: TripRequest = parse(
            r#"{"coordinates":[{"longitude":0.0,"latitude":0.0},
                               {"longitude":1.0,"latitude":1.0}],
                "source":"first","destination":"any","roundtrip":true}"#);
        assert_eq!(built.source.as_str(), parsed.source.as_str());
        assert_eq!(built.destination.as_str(), parsed.destination.as_str());
        assert_eq!(built.overview.as_str(), parsed.overview.as_str());
        assert_eq!(built.geometries.as_str(), parsed.geometries.as_str());
        assert_eq!(built.steps, parsed.steps);
        assert_eq!(built.annotations, parsed.annotations);
        assert_eq!(built.profile.as_str(), parsed.profile.as_str());
    }

    #[test]
    fn trip_caps_coordinates_at_two_hundred() {
        let many = (0..201).map(|i| format!(r#"{{"longitude":{}.0,"latitude":0.0}}"#, i % 90))
            .collect::<Vec<_>>().join(",");
        let request: TripRequest = parse(&format!(r#"{{"coordinates":[{many}]}}"#));
        assert_eq!(request.validate()[0].kind, "too_long");
    }
}
