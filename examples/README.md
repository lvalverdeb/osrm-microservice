# The examples

## What they are for

Two purposes, and everything below follows from them:

1. **Showcase the suite's functionality** — what it does, on work that looks
   like a customer's.
2. **Provide implementation clues** — an example should read as the code
   somebody integrating the suite would copy.

A third thing they must **not** be is *proof*. Proof is a test's job, and the
distinction is not cosmetic: proving a property tempts you into reaching for
internals, and an example that monkeypatches a private function shows a reader
a technique they cannot use. Two examples were doing exactly that and were
corrected; the rule is now enforced by `tests/test_examples_run.py` and by
`grep -rn "from vrp[a-z_.]* import.*\b_[a-z]" examples/src`.

## Conventions

- **Public API only.** If a demonstration needs a private hook, it is a test.
  Where an example legitimately needed something private, the library was
  changed — `must_be_served` moved to `vrp.model`, `build_large_matrix` gained
  `snap=`/`fetch=` seams — rather than the example reaching in.
- **Offline by default.** An example that needs infrastructure to run is one
  most readers never see work. Where a road matrix is wanted but no gateway is
  there, `dataset.road_matrix_or_planar` falls back to straight-line distances
  **and says so**, because a planar number is not the road's number.
- **Real work.** `dataset.py` slices the Costa Rica corpus; take a slice rather
  than inventing coordinates, unless the instance itself is the subject — a
  maximal record for serialisation, an adversarial fixture, a known speed
  profile whose ground truth is the point.
- **The docstring is the contract.** First line says what it shows; then the
  modules it demonstrates, the requirement it traces to, numbered sections, a
  statement about what it needs to run, and a usage line.
- **Run it.** `make examples-check` executes every one of them and is a CI
  step. Nothing ran them before, and two had rotted against a green suite.

## Where to look

**VRP feature examples** — one per capability, each tracing to a requirement.
Indexed in [`docs/planning/VRP_TDD_EXAMPLES.md`](../docs/planning/VRP_TDD_EXAMPLES.md)
by `E-nn`, with the task and requirements it demonstrates.

**Gateway examples** — the HTTP surface rather than the VRP library. These need
a running gateway; `examples/.env` points at one.

| example | shows |
|---|---|
| `routing/route_advanced_options.py` | `/route` and its full option surface |
| `routing/matrix_example.py` | `/matrix` |
| `routing/matrix_graph_example.py` | `/matrix-graph`, and the graph shape it returns |
| `routing/match_example.py` | `/match`, snapping a GPS trace to the network |
| `routing/nearest_example.py` | `/nearest` |
| `routing/tile_example.py` | `/tile`, and the protobuf it answers with |
| `routing/error_handling_demo.py` | what every failure mode looks like to a client |
| `infra/health_and_metrics.py` | `/health`, `/ready` and the Prometheus scrape |
| `fleet/clustering_mode_comparison.py` | the clustering modes, side by side |
| `fleet/hysteresis_demo.py` | the depot-assignment hysteresis band |
| `clustering/run_clustering_workflow.py` | a clustering request end to end |
| `clustering/simple_id_example.py` | custom stop identifiers through the API |

**Benchmarks** — `benchmarking/published_instances.py` scores the engine on
problems other people have already solved (§11.3, §13.3).

**Tools** — utilities rather than demonstrations. They are here because they
operate on the examples' data, and they are listed separately because a reader
looking for "how do I use this" should not have to sift them out.

| tool | does |
|---|---|
| `fleet/generate_delivery_dataset.py` | builds `data/deliveries_cr.json`, snapping every point through OSRM |
| `fleet/stress_test_vrp.py` | drives the VRP endpoint at volume |
| `fleet/visualize_vrp.py` | renders a plan to an HTML map |
| `routing/visualize_routes.py` | renders routes to an HTML map |
| `clustering/generate_payload.py` | builds a clustering request payload |
| `benchmarking/compare_tsp.py` | compares TSP results across engines |

## Running one

```
uv run --package osrm-api-gateway-examples examples/src/fleet/rich/ride_time.py
```

The `--package` matters: the examples are a separate workspace package, and
installing it is what puts `config` on `sys.path` and `folium` in the
environment. `make examples` is an interactive menu over the same set.
