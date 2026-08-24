//! Chunking, fan-out and the per-vehicle TSP.
//!
//! The TSP itself is not solved here: it is delegated to `osrm-routed`'s
//! `/trip`, and this module only partitions the work, bounds its concurrency,
//! and maps OSRM's optimised waypoint order back onto the caller's stop indices.

use std::sync::Arc;

use serde::Deserialize;
use serde_json::value::RawValue;
use serde_json::Value;
use tokio::sync::Semaphore;

use crate::models::{join_coordinates, Coordinate, MatrixRequest, Stop, TripRequest, VrpRequest};
use crate::osrm::client::{OsrmClient, OsrmError};
use crate::osrm::params;
use crate::vrp::allocate::{self, Allocation, AllocationOptions, KM_TO_M};

/// Everything one vehicle's TSP call needs.
#[derive(Debug, Clone)]
pub struct ChunkRequest {
    pub depot_idx: usize,
    pub depot: Coordinate,
    pub stops: Vec<Coordinate>,
    pub original_indices: Vec<usize>,
    pub stop_ids: Option<Vec<Value>>,
    pub vehicle_id: Value,
    pub roundtrip: bool,
}

/// One solved vehicle route.
#[derive(Debug, serde::Serialize)]
pub struct VehicleRoute {
    pub vehicle_id: Value,
    pub depot_index: usize,
    pub stops_indices: Vec<usize>,
    pub stop_ids: Option<Vec<Value>>,
    pub stop_coordinates: Option<Vec<Coordinate>>,
    pub route_geometry: Box<RawValue>,
    pub distance_meters: f64,
    pub duration_seconds: f64,
}

#[derive(Deserialize)]
struct TripResponse {
    code: String,
    #[serde(default)]
    trips: Vec<Trip>,
    #[serde(default)]
    waypoints: Vec<TripWaypoint>,
}

#[derive(Deserialize)]
struct Trip {
    /// Kept as raw bytes so the engine's coordinates are embedded unchanged.
    geometry: Box<RawValue>,
    distance: f64,
    duration: f64,
}

#[derive(Deserialize)]
struct TripWaypoint {
    #[serde(default)]
    trips_index: i64,
    #[serde(default)]
    waypoint_index: i64,
}

#[derive(Deserialize)]
struct TableResponse {
    #[serde(default)]
    durations: Vec<Vec<Option<f64>>>,
    #[serde(default)]
    distances: Vec<Vec<Option<f64>>>,
}

/// Partition one depot's stops into TSP-sized chunks.
///
/// Chunks are contiguous slices of the allocation order, with no geographic
/// sub-clustering -- membership is whatever order the stops arrived in. That is
/// the largest route-quality gap in the algorithm and is reproduced as-is.
///
/// Args:
///     request: The originating VRP request.
///     depot_idx: Depot these stops were allocated to.
///     stop_indices: Indices into `request.stops` for this depot.
///     vehicle_offset: Routes already built for earlier depots, used to number
///         vehicles when the depots carry no IDs.
pub fn build_chunk_requests(request: &VrpRequest, depot_idx: usize, stop_indices: &[usize],
                            vehicle_offset: usize, chunk_size_limit: usize) -> Vec<ChunkRequest> {
    let depot = &request.depots[depot_idx];
    let chunk_size = chunk_size_limit.min(request.capacity.max(1) as usize).max(1);
    let num_chunks = stop_indices.len().div_ceil(chunk_size);

    stop_indices.chunks(chunk_size).enumerate()
        .map(|(position, chunk)| {
            let ids: Vec<Value> = chunk.iter()
                .map(|&i| request.stops[i].id.clone().unwrap_or(Value::Null))
                .collect();
            let has_ids = ids.iter().any(|id| !id.is_null());
            ChunkRequest {
                depot_idx,
                depot: depot.coordinate(),
                stops: chunk.iter().map(|&i| request.stops[i].coordinate()).collect(),
                original_indices: chunk.to_vec(),
                stop_ids: has_ids.then_some(ids),
                vehicle_id: vehicle_label(depot, position, num_chunks, vehicle_offset),
                roundtrip: request.roundtrip,
            }
        })
        .collect()
}

/// Label one vehicle.
///
/// With a depot ID, routes are `<id>` alone or `<id>-<n>` when the depot needs
/// more than one. Without one, they count routes built so far -- which is why
/// the offset is passed in rather than read off a partially filled result list.
fn vehicle_label(depot: &Stop, position: usize, num_chunks: usize, offset: usize) -> Value {
    match &depot.id {
        Some(id) if !id.is_null() => {
            if num_chunks > 1 {
                let base = id.as_str().map(str::to_string).unwrap_or_else(|| id.to_string());
                Value::String(format!("{base}-{}", position + 1))
            } else {
                id.clone()
            }
        }
        _ => Value::from(offset + position),
    }
}

/// Map OSRM's optimised waypoint order back onto the caller's stop indices.
///
/// Waypoint 0 is the depot and is dropped; every other input index maps back
/// one place, because the depot was prepended to the coordinate list.
fn reorder_waypoints(waypoints: &[TripWaypoint], chunk: &ChunkRequest)
    -> Result<(Vec<usize>, Option<Vec<Value>>, Vec<Coordinate>), OsrmError> {
    let mut order: Vec<usize> = (0..waypoints.len()).collect();
    order.sort_by_key(|&i| (waypoints[i].trips_index, waypoints[i].waypoint_index));

    let mut indices = Vec::new();
    let mut ids = chunk.stop_ids.as_ref().map(|_| Vec::new());
    let mut coordinates = Vec::new();
    for input_index in order.into_iter().filter(|&i| i > 0) {
        let mapped = input_index - 1;
        if mapped >= chunk.original_indices.len() || mapped >= chunk.stops.len() {
            return Err(OsrmError::Unavailable(format!(
                "OSRM returned waypoint index {input_index} out of range for {} stops",
                chunk.original_indices.len())));
        }
        indices.push(chunk.original_indices[mapped]);
        coordinates.push(chunk.stops[mapped]);
        if let (Some(collected), Some(source)) = (ids.as_mut(), chunk.stop_ids.as_ref()) {
            collected.push(source[mapped].clone());
        }
    }
    Ok((indices, ids, coordinates))
}

/// Solve one chunk via `/trip`.
async fn solve_chunk(client: Arc<OsrmClient>, chunk: ChunkRequest)
    -> Result<VehicleRoute, OsrmError> {
    let mut coordinates = vec![chunk.depot];
    coordinates.extend(chunk.stops.iter().copied());
    let request = TripRequest::vrp_chunk(coordinates, chunk.roundtrip);

    let endpoint = format!("/trip/v1/driving/{}", join_coordinates(&request.coordinates));
    let bytes = client.get(&endpoint, &params::trip(&request)).await?;
    let response: TripResponse = serde_json::from_slice(&bytes)
        .map_err(|e| OsrmError::Unavailable(format!("undecodable trip response: {e}")))?;

    if response.code != "Ok" || response.trips.is_empty() {
        return Err(OsrmError::Unavailable("Failed to optimize TSP chunk".to_string()));
    }
    let (indices, ids, coordinates) = reorder_waypoints(&response.waypoints, &chunk)?;
    let best = &response.trips[0];
    Ok(VehicleRoute {
        vehicle_id: chunk.vehicle_id,
        depot_index: chunk.depot_idx,
        stops_indices: indices,
        stop_ids: ids,
        stop_coordinates: Some(coordinates),
        route_geometry: best.geometry.clone(),
        distance_meters: best.distance,
        duration_seconds: best.duration,
    })
}

/// Solve one depot's chunks concurrently, returning them in chunk order.
///
/// Bounded by `concurrency` so a single solve cannot saturate the engine, and
/// the first failure aborts its siblings rather than leaving them running
/// against OSRM for a response nobody will read.
pub async fn solve_depot_routes(client: Arc<OsrmClient>, chunks: Vec<ChunkRequest>,
                                concurrency: usize) -> Result<Vec<VehicleRoute>, OsrmError> {
    let slots = Arc::new(Semaphore::new(concurrency.max(1)));
    let mut set = tokio::task::JoinSet::new();
    for (position, chunk) in chunks.into_iter().enumerate() {
        let client = Arc::clone(&client);
        let slots = Arc::clone(&slots);
        set.spawn(async move {
            let _permit = slots.acquire().await.expect("semaphore is never closed");
            (position, solve_chunk(client, chunk).await)
        });
    }

    let mut solved: Vec<Option<VehicleRoute>> = Vec::new();
    while let Some(joined) = set.join_next().await {
        let (position, result) = joined
            .map_err(|e| OsrmError::Unavailable(format!("chunk task failed: {e}")))?;
        match result {
            Ok(route) => {
                if solved.len() <= position {
                    solved.resize_with(position + 1, || None);
                }
                solved[position] = Some(route);
            }
            Err(error) => {
                set.abort_all();
                return Err(error);
            }
        }
    }
    Ok(solved.into_iter().flatten().collect())
}

/// Fetch the depots-to-stops duration and distance matrices.
///
/// Batched so each `/table` call stays inside the same sources x destinations
/// budget the engine enforces. The batches run one at a time, as in Python:
/// parallelising them would change nothing about the result but would make a
/// parity diff ambiguous while the port is being validated.
pub async fn depot_to_stop_matrix(client: &OsrmClient, depots: &[Coordinate],
                                  stops: &[Coordinate], batch_size: usize,
                                  max_cells: usize) -> Result<(Vec<Vec<f64>>, Vec<Vec<f64>>), OsrmError> {
    let num_depots = depots.len().max(1);
    let batch = batch_size.min(max_cells / num_depots).max(1);

    let mut durations: Vec<Vec<f64>> = vec![Vec::new(); depots.len()];
    let mut distances: Vec<Vec<f64>> = vec![Vec::new(); depots.len()];

    for slice in stops.chunks(batch) {
        let mut coordinates = depots.to_vec();
        coordinates.extend_from_slice(slice);
        let sources = (0..depots.len() as i64).collect();
        let destinations = (depots.len() as i64..coordinates.len() as i64).collect();
        let request = MatrixRequest::vrp_batch(coordinates, sources, destinations);

        let endpoint = format!("/table/v1/driving/{}", join_coordinates(&request.coordinates));
        let bytes = client.get(&endpoint, &params::matrix(&request)).await?;
        let table: TableResponse = serde_json::from_slice(&bytes)
            .map_err(|e| OsrmError::Unavailable(format!("undecodable table response: {e}")))?;

        for (depot_idx, row) in allocate::prepare_matrix(&table.durations).into_iter().enumerate() {
            durations[depot_idx].extend(row);
        }
        for (depot_idx, row) in allocate::prepare_matrix(&table.distances).into_iter().enumerate() {
            distances[depot_idx].extend(row);
        }
    }
    Ok((durations, distances))
}

/// Matrix, then allocation -- the phase `/vrp` and `/vrp/allocate` share.
pub async fn allocation_for(client: &OsrmClient, request: &VrpRequest, batch_size: usize,
                            max_cells: usize, sanity_limit_m: f64) -> Result<Allocation, OsrmError> {
    let depots: Vec<Coordinate> = request.depots.iter().map(Stop::coordinate).collect();
    let stops: Vec<Coordinate> = request.stops.iter().map(Stop::coordinate).collect();
    let (durations, distances) =
        depot_to_stop_matrix(client, &depots, &stops, batch_size, max_cells).await?;

    Ok(allocate::allocate_stops(&durations, &distances, &depots, &stops, AllocationOptions {
        mode: request.clustering_mode,
        hysteresis_m: request.hysteresis_m,
        max_radius_m: request.max_radius_km.filter(|km| *km != 0.0).map(|km| km * KM_TO_M),
        sanity_limit_m,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn request(stops: usize, depots: usize, capacity: i64) -> VrpRequest {
        let depot_list: Vec<Value> = (0..depots)
            .map(|i| json!({"longitude": 0.0, "latitude": 0.0, "id": format!("d{i}")}))
            .collect();
        let stop_list: Vec<Value> = (0..stops)
            .map(|i| json!({"longitude": 0.1 * i as f64, "latitude": 0.0, "id": format!("s{i}")}))
            .collect();
        serde_json::from_value(json!({
            "depots": depot_list, "stops": stop_list, "capacity": capacity
        })).expect("valid request")
    }

    fn anonymous(stops: usize) -> VrpRequest {
        let stop_list: Vec<Value> = (0..stops)
            .map(|i| json!({"longitude": 0.1 * i as f64, "latitude": 0.0}))
            .collect();
        serde_json::from_value(json!({
            "depots": [{"longitude": 0.0, "latitude": 0.0}], "stops": stop_list, "capacity": 2
        })).expect("valid request")
    }

    #[test]
    fn chunks_are_bounded_by_capacity() {
        let req = request(5, 1, 2);
        let chunks = build_chunk_requests(&req, 0, &[0, 1, 2, 3, 4], 0, 80);
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks[0].original_indices, vec![0, 1]);
        assert_eq!(chunks[2].original_indices, vec![4]);
    }

    /// The bound is min(VRP_CHUNK_SIZE, capacity), not capacity alone.
    #[test]
    fn chunks_are_bounded_by_the_chunk_size_when_it_is_smaller() {
        let req = request(10, 1, 100);
        let chunks = build_chunk_requests(&req, 0, &(0..10).collect::<Vec<_>>(), 0, 4);
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks[0].original_indices.len(), 4);
    }

    #[test]
    fn a_single_chunk_takes_the_bare_depot_id() {
        let req = request(2, 1, 80);
        let chunks = build_chunk_requests(&req, 0, &[0, 1], 0, 80);
        assert_eq!(chunks[0].vehicle_id, json!("d0"));
    }

    #[test]
    fn multiple_chunks_get_a_suffixed_depot_id() {
        let req = request(4, 1, 2);
        let chunks = build_chunk_requests(&req, 0, &[0, 1, 2, 3], 0, 80);
        assert_eq!(chunks[0].vehicle_id, json!("d0-1"));
        assert_eq!(chunks[1].vehicle_id, json!("d0-2"));
    }

    /// Without depot IDs the label counts routes built so far, which is why the
    /// offset is passed in rather than read off a partial result list.
    #[test]
    fn without_depot_ids_vehicles_are_numbered_from_the_offset() {
        let req = anonymous(4);
        let chunks = build_chunk_requests(&req, 0, &[0, 1, 2, 3], 7, 80);
        assert_eq!(chunks[0].vehicle_id, json!(7));
        assert_eq!(chunks[1].vehicle_id, json!(8));
    }

    #[test]
    fn stop_ids_are_dropped_when_no_stop_carries_one() {
        let req = anonymous(2);
        let chunks = build_chunk_requests(&req, 0, &[0, 1], 0, 80);
        assert!(chunks[0].stop_ids.is_none());
    }

    #[test]
    fn stop_ids_are_kept_when_any_stop_carries_one() {
        let req = request(2, 1, 80);
        let chunks = build_chunk_requests(&req, 0, &[0, 1], 0, 80);
        assert_eq!(chunks[0].stop_ids, Some(vec![json!("s0"), json!("s1")]));
    }

    fn chunk_of(indices: Vec<usize>) -> ChunkRequest {
        ChunkRequest {
            depot_idx: 0,
            depot: Coordinate { longitude: 0.0, latitude: 0.0 },
            stops: indices.iter().map(|&i| Coordinate { longitude: i as f64, latitude: 0.0 })
                .collect(),
            original_indices: indices.clone(),
            stop_ids: Some(indices.iter().map(|i| json!(format!("s{i}"))).collect()),
            vehicle_id: json!(0),
            roundtrip: true,
        }
    }

    fn waypoints(order: &[i64]) -> Vec<TripWaypoint> {
        order.iter().map(|&waypoint_index| TripWaypoint { trips_index: 0, waypoint_index })
            .collect()
    }

    #[test]
    fn waypoints_map_back_to_original_stop_indices() {
        let chunk = chunk_of(vec![10, 11, 12]);
        // Depot first, then stops visited in reverse input order.
        let (indices, ids, coordinates) =
            reorder_waypoints(&waypoints(&[0, 3, 2, 1]), &chunk).unwrap();
        assert_eq!(indices, vec![12, 11, 10]);
        assert_eq!(ids, Some(vec![json!("s12"), json!("s11"), json!("s10")]));
        assert_eq!(coordinates.len(), 3);
    }

    #[test]
    fn the_depot_waypoint_is_dropped() {
        let chunk = chunk_of(vec![5, 6]);
        let (indices, _, _) = reorder_waypoints(&waypoints(&[0, 1, 2]), &chunk).unwrap();
        assert_eq!(indices, vec![5, 6]);
    }

    #[test]
    fn an_out_of_range_waypoint_is_an_error() {
        let chunk = chunk_of(vec![5]);
        // Three waypoints for one stop: index 2 maps past the end.
        assert!(reorder_waypoints(&waypoints(&[0, 1, 2]), &chunk).is_err());
    }

    #[test]
    fn ids_are_omitted_when_the_chunk_has_none() {
        let mut chunk = chunk_of(vec![1, 2]);
        chunk.stop_ids = None;
        let (_, ids, _) = reorder_waypoints(&waypoints(&[0, 1, 2]), &chunk).unwrap();
        assert!(ids.is_none());
    }
}
