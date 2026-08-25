//! Location-allocation: assign each stop to a depot.
//!
//! A single pass, no iteration and no convergence loop -- for each stop, the
//! cheapest depot by the chosen cost matrix, overridden by an air-distance
//! "anchor" when the matrix answer looks unreasonable. Ports of this go wrong
//! in the tie-breaking, so note that `numpy.argmin` returns the *lowest* index
//! among equals and Rust's `Iterator::min_by` returns the *first* -- the same
//! thing, given the same iteration order.

use crate::models::{ClusteringMode, Coordinate};
use crate::osrm::client::OsrmError;

/// Stands in for JSON `null` in a cost matrix: OSRM reports an unreachable
/// pair as null, and the comparisons below need a number.
pub const UNREACHABLE: f64 = 1e12;

/// 40 km/h in m/s, used to convert a metre-denominated hysteresis into seconds
/// when the mode is time-based.
const HYSTERESIS_SPEED_MS: f64 = 11.1111;

/// Approximate metres per degree of latitude.
const DEG_TO_M: f64 = 111_320.0;

pub const KM_TO_M: f64 = 1000.0;

/// Tuning knobs for one allocation pass.
#[derive(Debug, Clone, Copy)]
pub struct AllocationOptions {
    pub mode: ClusteringMode,
    pub hysteresis_m: f64,
    pub max_radius_m: Option<f64>,
    pub sanity_limit_m: f64,
}

/// Which depot serves each stop, and which stops nothing serves.
#[derive(Debug, Clone, PartialEq)]
pub struct Allocation {
    /// One entry per depot, in depot order, each listing its stop indices.
    pub assignments: Vec<Vec<usize>>,
    pub unreachable: Vec<usize>,
}

/// Replace nulls with the unreachable sentinel.
pub fn prepare_matrix(rows: &[Vec<Option<f64>>]) -> Vec<Vec<f64>> {
    rows.iter()
        .map(|row| row.iter().map(|cell| cell.unwrap_or(UNREACHABLE)).collect())
        .collect()
}

/// Straight-line distance in metres from one stop to every depot.
///
/// Equirectangular rather than haversine, with a single longitude scale taken
/// from the centroid of all stops -- computed once per request, not per stop.
fn euclidean_distances_m(stop: &Coordinate, depots: &[Coordinate], lon_scale: f64) -> Vec<f64> {
    depots.iter()
        .map(|depot| {
            let lat_m = (depot.latitude - stop.latitude) * DEG_TO_M;
            let lon_m = (depot.longitude - stop.longitude) * DEG_TO_M * lon_scale;
            (lat_m * lat_m + lon_m * lon_m).sqrt()
        })
        .collect()
}

/// Index of the smallest value, ties going to the lowest index.
fn argmin(values: &[f64]) -> usize {
    values.iter()
        .enumerate()
        .min_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(index, _)| index)
        .unwrap_or(0)
}

/// Pick the depot for one stop.
///
/// The order of these checks is the algorithm; changing it changes results.
fn select_depot(stop_idx: usize, euclidean_m: &[f64], target: &[Vec<f64>],
                mode: ClusteringMode, hysteresis: f64, sanity_limit_m: f64) -> usize {
    let anchor = argmin(euclidean_m);
    // Radial never consults the cost matrix at all: no hysteresis, no sanity.
    if mode == ClusteringMode::Radial {
        return anchor;
    }

    let column: Vec<f64> = target.iter().map(|row| row[stop_idx]).collect();
    let best = argmin(&column);
    let (best_val, anchor_val) = (column[best], column[anchor]);

    // Visual sanity: refuse a depot that is far further away as the crow flies,
    // however good the road cost looks.
    if euclidean_m[best] - euclidean_m[anchor] > sanity_limit_m {
        return anchor;
    }
    if best_val >= UNREACHABLE {
        return anchor;
    }
    if anchor_val >= UNREACHABLE {
        return best;
    }
    // Hysteresis: only move off the anchor for a clear improvement.
    if best_val < anchor_val - hysteresis {
        return best;
    }
    anchor
}

/// Assign every stop to a depot.
///
/// Args:
///     durations: depots x stops travel times.
///     distances: depots x stops road distances.
///     depots: Depot coordinates.
///     stops: Stop coordinates.
///     options: Mode, hysteresis, radius and sanity limit.
///
/// Returns:
///     The per-depot assignments and the stops ruled out by `max_radius_m`.
pub fn allocate_stops(durations: &[Vec<f64>], distances: &[Vec<f64>], depots: &[Coordinate],
                      stops: &[Coordinate], options: AllocationOptions)
    -> Result<Allocation, OsrmError> {
    let mut assignments = vec![Vec::new(); depots.len()];
    let mut unreachable = Vec::new();
    if stops.is_empty() {
        return Ok(Allocation { assignments, unreachable });
    }

    // The loop is driven by the matrix width, as `range(dist_np.shape[1])` is:
    // a short `/table` response leaves its trailing stops unassigned rather
    // than being indexed past. Iterating `0..stops.len()` instead read
    // `row[stop_idx]` out of bounds and panicked the worker.
    let width = matrix_width(distances, durations, depots.len(), stops.len())?;

    let target = if options.mode == ClusteringMode::TravelTime { durations } else { distances };
    // A metre-denominated band means nothing against a matrix of seconds, so it
    // is converted at an assumed 40 km/h for the time-based mode only.
    let hysteresis = if options.mode == ClusteringMode::TravelTime {
        options.hysteresis_m / HYSTERESIS_SPEED_MS
    } else {
        options.hysteresis_m
    };

    let centroid_lat = stops.iter().map(|s| s.latitude).sum::<f64>() / stops.len() as f64;
    let lon_scale = centroid_lat.to_radians().cos();

    for stop_idx in 0..width {
        let euclidean_m = euclidean_distances_m(&stops[stop_idx], depots, lon_scale);
        let depot = select_depot(stop_idx, &euclidean_m, target, options.mode,
                                 hysteresis, options.sanity_limit_m);

        let road_distance = distances[depot][stop_idx];
        // Two ways a stop goes unserved, per docs/features/clustering_modes.md.
        //
        // The engine has no route to it at all -- every depot answered null, so
        // even the best choice carries the sentinel. Assigning such a stop sent
        // it to `/trip` with the rest of its chunk, where OSRM answers NoRoute;
        // the chunk error then cancels its siblings, so a single unroutable stop
        // failed the whole request with a 500.
        let unroutable = road_distance >= UNREACHABLE;
        // Or it is routable but further away than the caller will accept.
        let beyond_radius = options.max_radius_m
            .is_some_and(|limit| road_distance > limit && road_distance < UNREACHABLE);
        if unroutable || beyond_radius {
            unreachable.push(stop_idx);
        } else {
            assignments[depot].push(stop_idx);
        }
    }
    Ok(Allocation { assignments, unreachable })
}

/// Width of the cost matrices, rejecting shapes that cannot be indexed.
///
/// Python built these with `np.array`, which raises on a ragged matrix, and
/// then indexed `stops[stop_idx]` inside a width-bounded loop, which raises
/// once the width exceeds the stop count. Both surfaced as 500 through the
/// solve's bare `except`, so the same shapes are refused here rather than
/// panicking on an unchecked index.
fn matrix_width(distances: &[Vec<f64>], durations: &[Vec<f64>], depots: usize, stops: usize)
    -> Result<usize, OsrmError> {
    let ragged = |matrix: &[Vec<f64>]| -> Option<usize> {
        let width = matrix.first().map(Vec::len).unwrap_or(0);
        matrix.iter().all(|row| row.len() == width).then_some(width)
    };
    let (Some(width), Some(duration_width)) = (ragged(distances), ragged(durations)) else {
        return Err(OsrmError::Unavailable("upstream cost matrix is ragged".to_string()));
    };
    if distances.len() != depots || durations.len() != depots || duration_width != width {
        return Err(OsrmError::Unavailable(
            "upstream cost matrix does not match the requested shape".to_string()));
    }
    if width > stops {
        return Err(OsrmError::Unavailable(
            "upstream cost matrix is wider than the stop list".to_string()));
    }
    Ok(width)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(longitude: f64, latitude: f64) -> Coordinate {
        Coordinate { longitude, latitude }
    }

    fn options(mode: ClusteringMode) -> AllocationOptions {
        AllocationOptions { mode, hysteresis_m: 2000.0, max_radius_m: None,
                            sanity_limit_m: 50_000.0 }
    }

    #[test]
    fn nulls_become_the_unreachable_sentinel() {
        assert_eq!(prepare_matrix(&[vec![Some(1.0), None]]), vec![vec![1.0, UNREACHABLE]]);
    }

    #[test]
    fn argmin_breaks_ties_toward_the_lowest_index() {
        // numpy.argmin does the same; a port that used the last minimum would
        // reallocate stops wherever two depots are equidistant.
        assert_eq!(argmin(&[5.0, 1.0, 1.0]), 1);
    }

    #[test]
    fn travel_time_mode_picks_the_faster_depot() {
        let depots = [at(0.0, 0.0), at(1.0, 0.0)];
        let stops = [at(0.5, 0.0)];
        let durations = vec![vec![900.0], vec![100.0]];
        let distances = vec![vec![9000.0], vec![1000.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::TravelTime)).expect("well-formed matrix");
        assert_eq!(result.assignments[1], vec![0]);
    }

    /// A marginal gain must not move a stop off its nearest depot.
    #[test]
    fn hysteresis_keeps_a_stop_on_its_anchor() {
        let depots = [at(0.0, 0.0), at(10.0, 0.0)];
        let stops = [at(0.1, 0.0)];
        // Depot 1 is nominally faster, but by less than 2000 / 11.1111 = 180 s.
        let durations = vec![vec![300.0], vec![200.0]];
        let distances = vec![vec![3000.0], vec![2000.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::TravelTime)).expect("well-formed matrix");
        assert_eq!(result.assignments[0], vec![0]);
    }

    /// Radial ignores the cost matrix entirely.
    #[test]
    fn radial_mode_uses_air_distance_only() {
        let depots = [at(0.0, 0.0), at(10.0, 0.0)];
        let stops = [at(0.1, 0.0)];
        let durations = vec![vec![9999.0], vec![1.0]];
        let distances = vec![vec![9999.0], vec![1.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::Radial)).expect("well-formed matrix");
        assert_eq!(result.assignments[0], vec![0]);
    }

    /// However good the road cost, a wildly more distant depot is refused.
    #[test]
    fn sanity_limit_overrides_an_absurd_matrix_answer() {
        let depots = [at(0.0, 0.0), at(10.0, 0.0)];
        let stops = [at(0.0, 0.0)];
        let durations = vec![vec![9999.0], vec![1.0]];
        let distances = vec![vec![9999.0], vec![1.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::TravelTime)).expect("well-formed matrix");
        assert_eq!(result.assignments[0], vec![0]);
    }

    #[test]
    fn an_unreachable_best_falls_back_to_the_anchor() {
        let depots = [at(0.0, 0.0), at(0.2, 0.0)];
        let stops = [at(0.1, 0.0)];
        let durations = vec![vec![UNREACHABLE], vec![UNREACHABLE]];
        let distances = vec![vec![100.0], vec![100.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::TravelTime)).expect("well-formed matrix");
        assert_eq!(result.assignments[0], vec![0]);
    }

    #[test]
    fn stops_beyond_the_radius_are_reported_unreachable() {
        let depots = [at(0.0, 0.0)];
        let stops = [at(0.1, 0.0)];
        let durations = vec![vec![600.0]];
        let distances = vec![vec![20_000.0]];
        let mut opts = options(ClusteringMode::TravelTime);
        opts.max_radius_m = Some(5000.0);
        let result = allocate_stops(&durations, &distances, &depots, &stops, opts).expect("well-formed matrix");
        assert_eq!(result.unreachable, vec![0]);
        assert!(result.assignments[0].is_empty());
    }

    /// A stop the engine cannot route to is reported, not assigned.
    ///
    /// Assigning it sends it to `/trip` with the rest of its chunk, OSRM answers
    /// NoRoute, and the chunk error cancels its siblings -- so one unroutable
    /// stop failed the entire request with a 500.
    #[test]
    fn an_unroutable_stop_is_reported_not_assigned() {
        let depots = [at(0.0, 0.0)];
        let stops = [at(0.1, 0.0)];
        let durations = vec![vec![UNREACHABLE]];
        let distances = vec![vec![UNREACHABLE]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::TravelTime)).expect("well-formed matrix");
        assert_eq!(result.unreachable, vec![0]);
        assert!(result.assignments[0].is_empty());
    }

    /// The same, with a radius configured -- the old guard only excluded the
    /// sentinel when `max_radius_m` was set, so this is the path that was wrong.
    #[test]
    fn an_unroutable_stop_is_reported_even_with_a_radius_set() {
        let depots = [at(0.0, 0.0)];
        let stops = [at(0.1, 0.0)];
        let durations = vec![vec![UNREACHABLE]];
        let distances = vec![vec![UNREACHABLE]];
        let mut opts = options(ClusteringMode::TravelTime);
        opts.max_radius_m = Some(5000.0);
        let result = allocate_stops(&durations, &distances, &depots, &stops, opts).expect("well-formed matrix");
        assert_eq!(result.unreachable, vec![0]);
        assert!(result.assignments[0].is_empty());
    }

    /// A reachable stop with no radius configured is still assigned: the
    /// sentinel check must not turn into a blanket rejection.
    #[test]
    fn a_reachable_stop_is_unaffected_by_the_sentinel_check() {
        let depots = [at(0.0, 0.0)];
        let stops = [at(0.1, 0.0)];
        let durations = vec![vec![600.0]];
        let distances = vec![vec![20_000.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::TravelTime)).expect("well-formed matrix");
        assert!(result.unreachable.is_empty());
        assert_eq!(result.assignments[0], vec![0]);
    }

    /// Python's loop ran over `dist_np.shape[1]`, so a short `/table` answer
    /// left its trailing stops unassigned. Iterating `0..stops.len()` instead
    /// read `row[stop_idx]` out of bounds and panicked the worker.
    #[test]
    fn a_short_matrix_leaves_its_trailing_stops_alone_rather_than_panicking() {
        let depots = vec![at(0.0, 0.0)];
        let stops = vec![at(0.1, 0.0), at(0.2, 0.0), at(0.3, 0.0)];
        // The engine answered for one stop of the three.
        let durations = vec![vec![60.0]];
        let distances = vec![vec![600.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops, options(ClusteringMode::TravelTime))
            .expect("a short matrix is not an error");
        assert_eq!(result.assignments[0], vec![0]);
        assert!(result.unreachable.is_empty());
    }

    /// `np.array` raises on a ragged matrix and indexing `stops[stop_idx]`
    /// raises once the width passes the stop count; both were 500s.
    #[test]
    fn an_unusable_matrix_shape_is_an_error() {
        let depots = vec![at(0.0, 0.0)];
        let stops = vec![at(0.1, 0.0)];
        let ragged = vec![vec![60.0, 120.0], vec![60.0]];
        assert!(allocate_stops(&ragged, &ragged, &depots, &stops, options(ClusteringMode::TravelTime)).is_err(),
                "ragged rows");
        let too_wide = vec![vec![60.0, 120.0]];
        assert!(allocate_stops(&too_wide, &too_wide, &depots, &stops, options(ClusteringMode::TravelTime)).is_err(),
                "wider than the stop list");
        let wrong_depth = vec![vec![60.0], vec![60.0]];
        assert!(allocate_stops(&wrong_depth, &wrong_depth, &depots, &stops, options(ClusteringMode::TravelTime)).is_err(),
                "more rows than depots");
    }

    #[test]
    fn a_single_depot_takes_everything() {
        let depots = [at(0.0, 0.0)];
        let stops = [at(0.1, 0.0), at(0.2, 0.0), at(0.3, 0.0)];
        let durations = vec![vec![10.0, 20.0, 30.0]];
        let distances = vec![vec![100.0, 200.0, 300.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::TravelTime)).expect("well-formed matrix");
        assert_eq!(result.assignments[0], vec![0, 1, 2]);
    }

    #[test]
    fn distance_mode_reads_the_distance_matrix() {
        let depots = [at(0.0, 0.0), at(1.0, 0.0)];
        let stops = [at(0.5, 0.0)];
        // Durations favour depot 0, distances favour depot 1 decisively.
        let durations = vec![vec![1.0], vec![9999.0]];
        let distances = vec![vec![90_000.0], vec![1000.0]];
        let result = allocate_stops(&durations, &distances, &depots, &stops,
                                    options(ClusteringMode::Distance)).expect("well-formed matrix");
        assert_eq!(result.assignments[1], vec![0]);
    }
}
