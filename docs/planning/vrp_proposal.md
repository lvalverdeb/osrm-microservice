# Proposal: VRP via OSRM Location-Allocation

This document outlines a strategy to solve **Vehicle Routing Problems (VRP)** using OSRM as the primary engine for both **Allocation** (assigning stops to vehicles) and **Sequencing) (optimal routing).

## 1. The Core Challenge: Location-Allocation

Traditional clustering (like K-Means or Sweep) fails in real-world logistics because it relies on Euclidean distance. Geographic barriers (rivers, dead-ends, mountain ranges) mean that a point "physically close" might be "logistically far."

## 2. Integrated Strategy (The "Allocate then Route" Pipeline)

### Phase 1: OSRM-Driven Allocation

Before optimizing routes, we must assign stops to vehicles based on actual road travel times:

1. **Depot-to-Stop Matrix**: Request a matrix from all **active depots** (warehouses) to all **unassigned delivery stops**.
2. **Greedy Road Assignment**:
   - For each stop, identify the depot with the **minimum duration** (not distance).
   - Assign the stop to the best depot.
   - *Constraint Handling*: If a depot/vehicle reaches a capacity limit, the stop is assigned to the next best "logistically available" depot.
3. **Partitioning**: Within a depot's fleet, further subdivide stops using a mini-matrix to ensure vehicles handle contiguous road clusters.

### Phase 2: Sequential Optimization (Local TSP)

Once a vehicle has its assigned stops, we use the **OSRM `/trip`** service:

- **Input**: The specific subset of stops for one vehicle + the warehouse address.
- **Parameters**: `roundtrip=True`, `source=first`.
- **Result**: The "best" sequence for that specific vehicle.

## 3. Proposed Architecture

### Workflow

1. **API Call**: `/vrp` receives $D$ depots, $V$ vehicles, and $S$ stops.
2. **Allocation Request**: Call `/matrix` where `sources = [depots]` and `destinations = [stops]`.
3. **Cost-Based Assignment**:
   - Loop through the matrix results.
   - Assign each stop to the vehicle/depot that can reach it fastest.
4. **Local Sequencing**: For each vehicle's final set, call `/trip`.
5. **Reconstruction**: Consolidate multiple GeoJSON routes into a single fleet response.

## 4. Why this approach?

- **Road Reality**: Assignments are based on how long it actually takes to drive, accounting for one-way streets and barriers.
- **Microservices Harmony**: It uses the same OSRM backend we already built, ensuring consistency.
- **Scalability**: By partitioning the problem, we avoid the exponential complexity of a global VRP.

## 5. Implementation Roadmap

1. **Phase 1**: Implement `AllocationService` to process duration matrices.
2. **Phase 2**: Implement `FleetManager` to coordinate multiple `/trip` calls.
3. **Phase 3**: Expose `POST /vrp` with multi-vehicle payload support.
