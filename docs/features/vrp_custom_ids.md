# VRP Unique ID Feature

This feature allows users to pass unique identifiers for stops in the `/vrp/allocate` endpoint, which are then preserved and returned in the allocation results. This is critical for robustly mapping results back to original data sources in complex ETL workflows.

## Features

- **Custom ID Support**: Stops can now include an `id` field (string or number).
- **ID Propagation**: The service returns these IDs in the cluster assignments instead of array indices.
- **Unreachable Stop Tracking**: Unreachable stops also reflect the provided IDs.
- **Backward Compatibility**: If no IDs are provided, the service falls back to using zero-based indices.

## Usage Example

### Request

```json
{
  "depots": [
    {"latitude": 9.9469, "longitude": -84.0558}
  ],
  "stops": [
    {"id": "ORDER_001", "latitude": 10.1, "longitude": -84.1},
    {"id": "ORDER_002", "latitude": 10.2, "longitude": -84.2}
  ],
  "clustering_mode": "travel_time"
}
```

### Response

```json
{
  "code": "Ok",
  "allocations": {
    "0": ["ORDER_001", "ORDER_002"]
  },
  "unreachable_stops": []
}
```

## Running the Clustering Workflow Example

The included `run_clustering_workflow.py` script has been updated to demonstrate this feature.

```bash
# Generate the payload with IDs
python examples/clustering/generate_payload.py

# Run the full workflow visualization
python examples/clustering/run_clustering_workflow.py
```

The resulting `.html` maps successfully use the IDs to lookup coordinates for visualization, demonstrating the robustness of this new approach.
