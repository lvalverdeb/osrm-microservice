# VRP Clustering Modes

The VRP solver's Location-Allocation phase uses a **clustering mode** to determine which depot (warehouse) each delivery stop is assigned to. Three modes are available, each optimized for different logistical realities.

---

## Mode Comparison

| Mode | Primary Metric | Speed | Accuracy | Best For |
|------|---------------|-------|----------|----------|
| `travel_time` | OSRM road duration | Fast | High | General-purpose logistics where delivery time matters most. |
| `distance` | OSRM road distance | Fast | High | Fuel-cost optimization or distance-constrained fleets. |
| `radial` | Euclidean (straight-line) | Fastest | Low | Quick initial partitioning, uniform terrain, no road barriers. |

---

## How Each Mode Works

### `travel_time` (default)

Selects the depot with the **shortest road travel duration** from the OSRM matrix. This accounts for one-way streets, speed limits, turn restrictions, and geographic barriers.

The hysteresis buffer is converted from meters to seconds using an assumed average speed of 40 km/h (`hysteresis_m / 11.111`), so a 2000m buffer becomes ~180 seconds.

**Recommended for:** Most delivery and logistics use cases.

### `distance`

Selects the depot with the **shortest road distance** from the OSRM matrix. Hysteresis is applied directly in meters.

**Recommended for:** Fuel-cost-sensitive operations, or when delivery time is not the primary constraint.

### `radial`

Assigns each stop to the depot with the **smallest Euclidean (straight-line) distance**. Ignores the OSRM road matrix entirely for the assignment decision (the matrix is still used later for TSP sequencing).

No hysteresis or sanity checks are applied — assignment is purely geometric.

**Recommended for:** Quick exploratory analysis, or terrain where road network data is unreliable.

---

## Hysteresis Buffer

The hysteresis buffer prevents **assignment flapping** — a stop near the boundary between two depots flipping its assignment between requests due to minor measurement variations.

### How It Works

For each stop, the algorithm identifies:
- **Anchor depot**: The depot closest by Euclidean distance (visual/geographic nearest).
- **Best depot**: The depot with the lowest cost in the selected mode's matrix.

The stop is assigned to the **best depot** only if its cost is strictly better than the anchor depot's cost by more than the hysteresis threshold. Otherwise, the stop stays with its **anchor depot**.

```
if best_cost < anchor_cost - hysteresis:
    assign to best_depot
else:
    assign to anchor_depot
```

### Default Behavior

| Setting | Value | Effect |
|---------|-------|--------|
| `hysteresis_m` | 2000.0 | A stop stays with its anchor unless the other depot is >2 km faster (by road). |
| `VRP_SANITY_LIMIT_M` | 50000.0 | If the best depot is >50 km farther by Euclidean than the anchor, force the anchor (sanity override). |

### Configuration

Adjust via the `VrpRequest` body or environment defaults:

```json
{
  "hysteresis_m": 1500.0,
  "clustering_mode": "travel_time"
}
```

Or via environment variables (see [Configuration](../configuration.md)):
```
VRP_HYSTERESIS_M=1500.0
VRP_SANITY_LIMIT_M=50000.0
```

---

## Visual Sanity Check

When the Euclidean distance to the cost-optimal depot exceeds the **anchor depot** by more than `VRP_SANITY_LIMIT_M` (default 50 km), the stop is forced to its anchor depot. This prevents counter-intuitive assignments where road data suggests a far-away depot is "better" due to a single fast highway connection.

---

## Unreachable Stops

A stop is marked **unreachable** when:
- `max_radius_km` is set and the road distance from the assigned depot exceeds that limit.
- The assigned depot has no valid road connection (`UNREACHABLE` sentinel in the matrix).

Unreachable stops are returned in the `unreachable_stops` array of both `VrpResponse` and `VrpAllocationResponse`.

---

## Running the Comparison Example

The project includes a script that runs the same dataset through all three modes:

```bash
uv run examples/src/vrp/clustering_mode_comparison.py
```

This generates side-by-side interactive HTML maps showing how each mode partitions stops across depots.
