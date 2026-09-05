# OSRM Backend Microservice

Routing, map-matching and vehicle-routing services for Costa Rica.

Two bodies of work live here, and it is worth knowing which one you are looking
at before reading further:

| | What it is | Where |
|---|---|---|
| **The gateway** | A Rust (axum) HTTP service in front of `osrm-routed`. This is what deploys. | [`gateway/`](gateway) |
| **The VRP platform** | A Python domain model, canonical evaluator and independent verifier for constrained fleet routing. A library, not yet a service. | [`vrp/`](vrp) |

They meet over HTTP: `vrp/` builds its travel matrices by calling the gateway's
`/matrix`, so the gateway owns transport — talking to the engine, retries,
chunking, caching — and `vrp/` owns the model and the solvers.

## Repository map

| Directory | Contents |
|---|---|
| [`gateway/`](gateway) | The Rust gateway. The only thing this repository builds and ships. |
| [`vrp/`](vrp) | Vehicle-routing domain model, evaluator, verifier and solver adapters. |
| [`deploy/`](deploy) | Both deployment options: Docker and FreeBSD jail. |
| [`docs/`](docs) | Specifications, API reference, runbook, deployment and feature guides. |
| [`examples/`](examples) | Runnable client scripts, its own workspace package. |
| [`tests/`](tests) | Black-box tests against the compiled gateway, plus the `vrp/` suite. |
| [`parity/`](parity) | Differential harness that diffs two gateway implementations. |
| [`loadtest/`](loadtest) | Arrival-rate load generator. |
| [`benchmarks/`](benchmarks) | Published VRP/TSP instances and the recorded baseline. |
| [`rust-spike/`](rust-spike) | An evaluation spike kept as a benchmark target, not a gateway. |

> **The `osrm-api-gateway` PyPI package is no longer maintained.**
> [0.2.1](https://pypi.org/project/osrm-api-gateway/0.2.1/) is its final release.
> The gateway was a FastAPI application; it is now the Rust binary in
> [`gateway/`](gateway) and is not distributed on PyPI. Existing installs keep
> working, but no further releases will be made — see
> [docs/planning/SCALING_READINESS_PLAN.md](docs/planning/SCALING_READINESS_PLAN.md)
> for what changed and what it was measured to be worth. The `pyproject.toml`
> here now packages the development tooling only.

## Deployment

The project supports **two** deployment options. Both run the same three services
— the OSRM engine, a Redis cache and the gateway — and everything either
one needs lives under [`deploy/`](deploy).

| Option | Files | Start with | When |
|---|---|---|---|
| **Docker** | [`deploy/docker/`](deploy/docker) | `make compose-up` | Any Linux Docker host, local or remote |
| **FreeBSD jail** | [`deploy/freebsd/`](deploy/freebsd) | `make jail-up` | A jail on a FreeBSD host, which cannot run Docker |

Full instructions for both, including prerequisites and Apple Silicon notes, are
in **[docs/deployment.md](docs/deployment.md)**. `make help` lists every target.

### Docker, in short

Data is processed into an image on your machine and bundled into the runtime
image by the multi-stage `deploy/docker/Dockerfile.osrm`, so nothing is
bind-mounted and the stack can be deployed to a remote Docker host as-is.

```bash
make download-data              # fetch the Costa Rica extract into ./data
make process-osrm PROFILE=car   # extract / partition / customize

export DOCKER_HOST=ssh://developer@10.211.55.36   # optional: target a remote daemon
make compose-doctor             # show the active Docker host and architecture
make compose-up                 # build and start, with sequencing and health checks
make compose-logs
make compose-health
make compose-down
```

Avoid `docker compose down & docker compose up --build`; the `&` backgrounds the
first command and the two race.

### FreeBSD jail, in short

A jail cannot run Docker — jails share the FreeBSD kernel, Docker needs Linux
namespaces and cgroups — so the same services run natively from packages and rc.d
scripts. See [docs/deployment_freebsd.md](docs/deployment_freebsd.md).

```bash
make jail-doctor      # check the target and how to escalate
make jail-bootstrap   # packages and service user
make jail-data        # build OSRM data in the jail
make jail-up          # deploy the gateway and start all services
```

### Running the gateway alone

Against an OSRM engine you already have:

```bash
cargo build --manifest-path gateway/Cargo.toml
OSRM_BASE_URL=http://127.0.0.1:5000 HOST=127.0.0.1 PORT=8000 \
  ./gateway/target/debug/osrm-api-gateway
```

## The gateway

Sixteen routes: six relay an OSRM service, three compute something OSRM does
not, three are infrastructure and four serve the API documentation.

| Endpoint | Does |
|---|---|
| `POST /route` | Route between two points, with alternatives and turn-by-turn steps |
| `POST /matrix` | Duration/distance matrix (capped at 100 coordinates by the engine) |
| `POST /matrix-graph` | The same matrix as a node-link graph |
| `POST /match` | Map-match a GPS trace to the road network |
| `POST /trip` | TSP over a set of coordinates |
| `POST /nearest` | Snap a coordinate to the nearest road segments |
| `POST /vrp` | Allocation plus per-vehicle sequencing |
| `POST /vrp/allocate` | The allocation phase alone |
| `GET /tile/{profile}/{z}/{x}/{y}.mvt` | Mapbox Vector Tile of the routing graph. The `.mvt` suffix is part of the path; the handler returns 404 without it. |
| `GET /health`, `/ready`, `/metrics` | Liveness, readiness, and Prometheus metrics. The last is served wherever `METRICS_ENDPOINT` points, `/metrics` by default. |
| `GET /docs`, `/redoc`, `/openapi.json` | Interactive API documentation |

Its routing logic lives in a few modules under [`gateway/src/`](gateway/src):

### 1. OSRM client (`osrm/client.rs`)
Talks to the C++ OSRM backend: builds each service's query, retries transport
failures and 5xx with exponential backoff, and relays the engine's bytes
verbatim so response numbers are never re-encoded. Both cache tiers live behind
it.
**Example use case**: fetching the geometry and driving instructions for a trip
between a warehouse and several delivery points.

### 2. Graph builder (`build_graph`, in `handlers.rs`)
Turns a duration/distance matrix into a directed node-link graph, reproducing
the shape `networkx.node_link_data` emits — the gateway does not depend on
networkx, it matches its output byte for byte so existing consumers keep
working.
**Example use case**: producing a mathematical representation of the road
network to feed a custom optimiser, or to find isolated nodes in a delivery
network.

### 3. VRP heuristic (`vrp/allocate.rs`, `vrp/solve.rs`)
Location-allocation in two phases. `allocate` assigns each stop to a depot by
road cost, with a hysteresis band that keeps territories stable between runs and
a sanity override that refuses an implausible matrix answer. `solve` orders each
depot's stops by sweep angle, cuts them into vehicle loads of at most `capacity`
stops, and sequences every load through OSRM's `/trip`, fanning the chunks out
concurrently.
**Example use case**: distributing 500 daily packages across 5 drivers from 2
warehouses, each driver taking a contiguous cluster of stops.

This is a fast heuristic with no notion of time windows, skills or driver hours.
For those, see the VRP platform below.

## The VRP platform (`vrp/`)

Implements the specification in
[docs/vrp-spec-driven-development.md](docs/vrp-spec-driven-development.md)
(`SDD-VRP-001`) against the scenario catalogue in
[docs/TDD/vrp-catalogue-v2.1.md](docs/TDD/vrp-catalogue-v2.1.md) (`CAT-VRP-003`:
142 operational scenarios and 15 adversarial instances, drawn from documented
deployments).

It is a library. There is no HTTP surface for it yet — how the Rust gateway
reaches a Python solver is an open architectural question the specification
deliberately leaves open — so it is driven from tests and from the example
scripts under `examples/src/fleet/`.

Two constitutional principles shape it and explain the module layout:

- **Feasibility is a gate, cost is a target** (`CON-1`). No plan is claimed
  feasible without passing [`vrp/verify/verifier.py`](vrp/verify/verifier.py),
  which shares no code with any solver. That independence is enforced by what
  the module is allowed to import, checked in the tests.
- **Model the business, then choose the algorithm** (`CON-3`). The domain model
  in [`vrp/model.py`](vrp/model.py) is defined without reference to any solver;
  OR-Tools and PyVRP sit behind it as adapters in
  [`vrp/solve/`](vrp/solve), and an agreement test drives both from the same
  `Problem`.

| Area | Modules |
|---|---|
| Core | `model.py`, `objective.py`, `evaluator.py`, `verify/verifier.py` |
| Data | `matrix.py`, `osrm.py`, `generate.py`, `icd.py`, `diagnose.py` |
| Constraints | `hos/` (hours of service), `zones.py`, `locks.py`, `periodic.py` |
| Search | `solve/` (OR-Tools, PyVRP), `lns.py`, `localsearch.py`, `setpartition.py`, `polish.py`, `portfolio.py` |
| Dynamic | `epochs.py`, `committed.py`, `pcdispatch.py`, `triggers.py`, `synchronise.py` |
| Learning | `calibrate.py`, `adherence.py`, `rollout.py`, `replay.py` |
| Fleet | `allocate.py`, `depots.py`, `fleet.py`, `decompose.py` |
| Explanation | `explain.py`, `consistency.py`, `stability.py` |
| Benchmarks | `bench/` against the published instances in [`benchmarks/`](benchmarks) |

## Client usage

```python
import requests

BASE_URL = "http://localhost:8000"

# 1. Route plotting (walking profile)
route_res = requests.post(f"{BASE_URL}/route", json={
    "origin": {"longitude": -84.0907, "latitude": 9.9281},
    "destination": {"longitude": -84.0833, "latitude": 9.9333},
    "profile": "walking",
    "steps": True,
})

# 2. Nearest point (road snapping)
nearest_res = requests.post(f"{BASE_URL}/nearest", json={
    "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
    "number": 3,
})

# 3. Travelling salesperson problem
tsp_res = requests.post(f"{BASE_URL}/trip", json={
    "coordinates": [
        {"longitude": -84.0907, "latitude": 9.9281},
        {"longitude": -84.0833, "latitude": 9.9333},
        {"longitude": -84.1107, "latitude": 9.9981},
    ],
})

# 4. Allocation only: which depot serves which stop
cluster_res = requests.post(f"{BASE_URL}/vrp/allocate", json={
    "depots": [{"id": "D1", "longitude": -84.0907, "latitude": 9.9281}],
    "stops": [
        {"id": "L1", "longitude": -84.0833, "latitude": 9.9333},
        {"id": "L2", "longitude": -84.1107, "latitude": 9.9981},
    ],
    "clustering_mode": "travel_time",
})

# 5. Full VRP: allocation plus a sequenced route per vehicle.
#    `capacity` is the knob that decides how many vehicles you get -- each
#    depot's stops are cut into loads of at most this many. It defaults to 35.
vrp_res = requests.post(f"{BASE_URL}/vrp", json={
    "depots": [{"id": "D1", "longitude": -84.0907, "latitude": 9.9281}],
    "stops": [
        {"id": "L1", "longitude": -84.0833, "latitude": 9.9333},
        {"id": "L2", "longitude": -84.1107, "latitude": 9.9981},
    ],
    "capacity": 1,
})
```

`vehicle_count` is accepted on both VRP endpoints and validated, but the solver
never reads it; it survives only so requests written against the retired Python
gateway keep their status codes. Use `capacity` to control the split.

## Examples

Runnable scripts under [`examples/src/`](examples/src), grouped by what they
demonstrate. Each writes an interactive folium map or prints a comparison table.

| Group | Covers |
|---|---|
| `routing/` | Routes and alternates, advanced options, matrices, matrix-to-graph, road snapping, map matching, vector tiles, and eight error scenarios |
| `clustering/` | Custom stop IDs, payload generation, a 6500-stop road-distance vs travel-time workflow |
| `benchmarking/` | Actual vs TSP-optimised sequences; the published instances in `benchmarks/` |
| `infra/` | Health probe, Prometheus metrics, caching, retry and logging |
| `fleet/` | The VRP platform, below — much the largest group |

The `fleet/` tree mirrors the specification's own structure:

| Subtree | Demonstrates |
|---|---|
| `fleet/` (top level) | Multi-warehouse VRP, hysteresis, clustering modes, objective modes, a 2500-stop stress test |
| `fleet/rich/` | Time windows, heterogeneous fleets, multi-trip, multi-period, hours of service, skills, synchronisation, decomposition, time-dependent travel, the engine portfolio |
| `fleet/dynamic/` | Dispatch waves, mid-day breakdown, preemption, committed state, prize-collecting epochs, replay |
| `fleet/alloc/` | Territories, fleet mix, fleet minimisation, tactical sizing, depot inventory |
| `fleet/learn/` | Service-time calibration, plan adherence, canary rollout, zone sequence priors |
| `fleet/tw/` | Multiple time windows per stop, and what lateness costs |
| `fleet/infra/` | Behaviour when the travel matrix degrades |
| `fleet/explain/` | Why a stop went unassigned; preflight diagnosis |
| `fleet/verify/` | Checking a plan produced by an external system |
| `fleet/adversarial/`, `fleet/p0/` | Pathological instances, and the must-work-at-v1 scenarios |

**Running them.** The examples are their own workspace package, so `--package` is
required: the workspace shares one `.venv` at the repository root, and a bare
`uv run` there syncs it to the root package and evicts folium, requests and
pydantic-settings. The gateway URL comes from `examples/.env`.

```bash
# Interactive menu (discovers all scripts automatically)
make examples

# ...which is shorthand for:
uv run --package osrm-api-gateway-examples examples/main.py

# Or run one directly
uv run --package osrm-api-gateway-examples examples/src/routing/matrix_example.py
uv run --package osrm-api-gateway-examples examples/src/fleet/rich/hours_of_service.py
uv run --package osrm-api-gateway-examples examples/src/infra/health_and_metrics.py
```

Maps are saved as interactive HTML files (`map.html`, `comparison_map.html`).

## Development

Python dependencies install into a `uv`-managed virtual environment:

```bash
uv pip install -e ".[dev]"
```

| Command | Runs |
|---|---|
| `make test` | The pytest suite: black-box tests against the compiled gateway, the `vrp/` suite, and the parity harness |
| `make lint` | `ruff check .` (`--fix` to apply) |
| `cargo test --manifest-path gateway/Cargo.toml` | The gateway's own unit tests |
| `make corpus` | The P0 scenario corpus at all three sizes; slow, excluded from `make test` |
| `make catalogue` | Rebuild `docs/TDD/vrp-catalogue-v2.1.md` from its source |
| `make parity` | Diff two gateway implementations over a seeded corpus |
| `make parity-selfcheck` | Validate the parity harness itself; offline, no engine needed |
| `make capacity` | Full capacity assessment with an OOM guard |

CI (`.github/workflows/tests.yml`) runs two jobs: `make test` plus `make lint`
with a built gateway and a pulled OSRM image, and `cargo test` for the gateway
with a `cargo check` on the spike.

Some tests drive a real gateway: they start the compiled binary as a
subprocess against a replay engine. `make test` does not build it, so build it
first or those tests skip with a message telling you to —

```bash
cargo build --manifest-path gateway/Cargo.toml
```

Under CI they fail instead of skipping, which is what catches a broken build
step. `pyvrp` and `ortools` are in the `dev` extra rather than an optional group
on purpose — a solver-agreement test that skips because an engine is missing
proves nothing.

## Load testing

`loadtest/run.py` drives a running gateway at a fixed **arrival rate** — requests
are launched on a schedule rather than after the previous one returns, so a slow
server shows up as rising latency instead of a quietly lower rate. Payloads are
randomised per request, which also keeps the L1/Redis caches from answering
everything. It uses `httpx`, already a project dependency; nothing to install.

Every endpoint has a scenario — `health`, `metrics`, `route`, `nearest`,
`matrix`, `matrix-graph`, `trip`, `match`, `tile`, `vrp`, `vrp-allocate` — and
the default `mixed` fires a weighted blend of all of them concurrently, which is
what a real client population looks like. Mixed runs print a per-endpoint
breakdown.

```sh
make loadtest                                              # mixed @ 25/s for 30s, localhost
make loadtest LOADTEST_URL=http://10.211.55.33:8000        # against a deployed jail
make loadtest LOADTEST_SCENARIO=matrix LOADTEST_RATE=5
make loadtest LOADTEST_SCENARIO=vrp LOADTEST_RATE=1 LOADTEST_ARGS="--size 500"
```

```
mixed @ 30.0/s for 25.0s -> http://10.211.55.33:8000
  requests   751 in 25.1s (29.9/s completed)
  latency    p50=53ms p95=91ms p99=177ms max=366ms
  endpoint                 n     p50     p95     p99   err%  statuses
  /route                 272     53m     85m     89m   0.0%  200=272
  /nearest               123     53m     86m    145m   0.0%  200=123
  /vrp                    18    150m    177m    366m   0.0%  200=18
  ...
```

| Variable | Default | Meaning |
|---|---|---|
| `LOADTEST_URL` | `http://127.0.0.1:8000` | Target gateway |
| `LOADTEST_SCENARIO` | `mixed` | Any single endpoint, or `mixed` for the blend |
| `LOADTEST_RATE` | `25` | Requests launched per second |
| `LOADTEST_DURATION` | `30` | Seconds |
| `LOADTEST_ARGS` | — | Passed through, e.g. `--size 500 --seed 42` |

Thresholds turn a run into a pass/fail gate — the target exits non-zero when one
is exceeded:

```sh
make loadtest LOADTEST_ARGS="--max-p95 0.5 --max-error-rate 0.01"
```

Four things to keep in mind when reading the numbers:

- **Rate limits are per client IP** (`RATE_LIMIT_ROUTE` 600/min, `RATE_LIMIT_VRP`
  100/min) and use fixed windows, so a short run can straddle two windows and
  never trip. Measured: 100/s for 8s → 547×200 and 254×429. Sustained runs above
  the limit measure the rate limiter, not capacity — a single-source run cannot
  exceed one bucket. Use `--forwarded-for-pool N` against a gateway configured
  with a matching trusted-proxy list to measure the service instead.
  Behind a reverse proxy the client IP is the *proxy's*, so all clients share one
  bucket until the deployment names its proxies — see
  [docs/deployment.md](docs/deployment.md), "Scaling".
- **`/matrix` is capped at 100 coordinates** by `osrm-routed`'s
  `--max-table-size` default, whatever the schema allows. The generator clamps
  to that.
- **One uvicorn worker by default**, on both deployments. VRP work is CPU-bound,
  so concurrency is not parallelism; raise `API_WORKERS` (Docker) or
  `JAIL_API_WORKERS` (jail) before drawing conclusions.
- **Memory scales with payload size, not request count.** Measured on a 2 GB
  jail: flat RSS across 400 sequential `/route` calls, but roughly +70 MB per
  1000 VRP stops in flight. Bound the jail with
  `rctl -a jail:api:memoryuse:deny=1g` before pushing a shared host hard.

## Configuration

All settings are controlled via environment variables (or a `.env` file).
[`deploy/env/app.env`](deploy/env/app.env) is shared by both deployments. See the
[Configuration Reference](docs/configuration.md) for the complete list.

## Documentation

Interactive API documentation is available once the service is running, at
`http://localhost:8000/docs` (Swagger UI) and `/redoc`.

| Document | Covers |
|---|---|
| [RUNBOOK.md](docs/RUNBOOK.md) | Start to finish: data, gateway, clients, both deployments. Read this first. |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | Request and response shapes for every endpoint |
| [configuration.md](docs/configuration.md) | Every setting and where it is read |
| [deployment.md](docs/deployment.md) | Both deployments, side by side |
| [deployment_freebsd.md](docs/deployment_freebsd.md) | Jail internals, host setup, pf |
| [dataset_prep.md](docs/dataset_prep.md) | Preparing OSRM data |
| [SDD.md](docs/SDD.md) | How the gateway is built and why |
| [vrp-spec-driven-development.md](docs/vrp-spec-driven-development.md) | `SDD-VRP-001`: the VRP platform's authoritative specification |
| [TDD/vrp-catalogue-v2.1.md](docs/TDD/vrp-catalogue-v2.1.md) | `CAT-VRP-003`: the real-world scenario catalogue the VRP work is tested against |
| [planning/](docs/planning) | Scaling readiness, the VRP proposal, fit-gap and example plans |

### Feature guides

| Feature | Description |
|---------|-------------|
| [Response Caching](docs/features/caching.md) | L1/L2 Cache-Aside strategy with in-memory and Redis layers. |
| [VRP Clustering Modes](docs/features/clustering_modes.md) | `travel_time`, `distance`, and `radial` allocation with hysteresis. |
| [Observability](docs/features/observability.md) | Structured logging, Prometheus metrics, OpenTelemetry tracing, health checks. |
| [Rate Limiting](docs/features/rate_limiting.md) | Per-endpoint request limits and configuration. |
| [VRP Custom IDs](docs/features/vrp_custom_ids.md) | Passing your own stop identifiers through allocation instead of array indices. |

## Components

- **OSRM Engine**: C++ routing powerhouse running the MLD algorithm.
- **Gateway**: An async Rust service (axum) providing specialised endpoints for
  map matching, graph generation, and vehicle routing.
- **Redis**: Optional second cache tier behind the gateway's in-process L1 cache.
- **VRP platform**: A Python domain model with pluggable OR-Tools and PyVRP
  adapters, and an independent feasibility verifier that shares no code with them.
