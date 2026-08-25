# Runbook

Start to finish: get map data, run the gateway, drive it from the example
clients, and deploy it either way.

This is the spine. It links out rather than restating, because the detail lives
in documents that are maintained alongside the thing they describe:

| For | Read |
|---|---|
| Both deployments, side by side | [deployment.md](deployment.md) |
| Jail internals, host setup, pf | [deployment_freebsd.md](deployment_freebsd.md) |
| Every setting and where it is read | [configuration.md](configuration.md) |
| Request and response shapes | [API_REFERENCE.md](API_REFERENCE.md) |

`make help` lists every target. Run it first if you only need a reminder.

---

## 0. What you are deploying

One Rust binary (`gateway/`) in front of `osrm-routed`, with Redis as an
optional second cache tier. The gateway is the only thing this repository
builds; the engine and Redis are stock.

There is no Python gateway any more. If you find a document referring to
`src/app/`, `osrm_client.py`, `graph_builder.py` or `vrp_service.py`, it predates
the port and is stale — the README's "Core Services" section is currently in
that state.

---

## 1. Build the data

OSRM cannot route until the map extract has been processed into its own format.
This is the slowest step and you only repeat it when the map changes.

```sh
# ~180 MB from Geofabrik into ./data
make download-data

# Extract, partition and customise it for one profile
make process-osrm            # PROFILE=car by default
make process-osrm PROFILE=bicycle
```

`download-data` is plain `curl`; `process-osrm` builds a Docker image that runs
`osrm-extract`/`osrm-partition`/`osrm-customize`, so **it needs Docker even if
you are heading for the jail.** The jail builds its own data natively —
see step 3.

Defaults, all overridable on the command line:

| Variable | Default |
|---|---|
| `DATA_DIR` | `./data` |
| `OSM_FILE` | `costa-rica-latest.osm.pbf` |
| `GEO_URL` | Geofabrik Central America |
| `PROFILE` | `car` |

Point `GEO_URL`/`OSM_FILE` elsewhere for a different region. `make clean`
removes both the download and the processed output.

### Skipping the real map entirely

For exercising API behaviour — validation, error shapes, metrics, the docs
pages — you do not need Costa Rica. `tests/synthetic/grid.osm` is a tiny grid
that processes in seconds with a local OSRM toolchain:

```sh
cd /tmp && cp ~/TeamDev/osrm-microservice/tests/synthetic/grid.osm .
osrm-extract -p /opt/homebrew/share/osrm/profiles/car.lua grid.osm
osrm-partition grid.osrm && osrm-customize grid.osrm
osrm-routed --algorithm mld grid.osrm --port 5000
```

Routing results on it are meaningless. Every API behaviour is real.

---

## 2. Run the examples

The example clients are a separate `uv` workspace package under `examples/`.
They talk to a **running gateway** — start one first (step 3 or 4), or point
them at any reachable instance.

```sh
make examples
```

That is an interactive menu that discovers the scripts. It is shorthand for:

```sh
uv run --package osrm-api-gateway-examples examples/main.py
```

The `--package` flag is load-bearing. The workspace shares one `.venv` at the
repository root, and a bare `uv run` syncs it to the *root* package, evicting
the examples' dependencies.

### Pointing them somewhere else

`examples/.env` sets `OSRM_API_URL`, defaulting to `http://localhost:8000`.
Copy `examples/.env.example` if you do not have one. That covers a tunnelled
jail, a remote Docker host, or a gateway on another port.

### What is in there

| Area | Scripts |
|---|---|
| `routing/` | route plotting, nearest, matrix, matrix-graph, match, tile, advanced options, error handling |
| `vrp/` | vehicle routing, visualisation, clustering-mode comparison, stress test |
| `clustering/` | allocation workflow, custom stop IDs |
| `benchmarking/` | TSP comparison, payload generation |
| `infra/` | health and metrics probe |

Several write an HTML map next to themselves and open it.

---

## 3. Deploy to the FreeBSD jail

Native — a jail cannot run Docker, so the gateway, engine and Redis all run as
`rc.d` services. Full detail in [deployment_freebsd.md](deployment_freebsd.md);
this is the order of operations.

```sh
make jail-doctor      # identity, arch, memory, escalation method — run this first
make jail-host        # one-time: kernel tunables, jail.conf loopback, resolver
make jail-bootstrap   # one-time: packages + service user
make jail-data        # build OSRM data natively in the jail (PROFILE=car)
make jail-up          # deploy the gateway and start all three services
make jail-health      # confirm API and OSRM are answering
```

Every `jail-*` target implies `jail-stage`, which uploads sources,
`deploy/freebsd/` and `deploy/env/`. You do not run it yourself.

`jail-host` is separate from `jail-up` on purpose: it edits the **host**, not
the jail, and needs escalation. [deployment_freebsd.md](deployment_freebsd.md)
explains what it changes and why it is not automatic.

Day to day:

```sh
make jail-logs        # gateway log + system messages
make jail-down        # stop all three
```

### Reaching it from outside

The jail is on a private loopback. Two ways in, both in
[deployment_freebsd.md](deployment_freebsd.md#reaching-the-gateway-from-outside-the-host):

```sh
# A: SSH tunnel, no host changes
ssh -L 8000:<jail-ip>:8000 <host>

# B: publish it — edits the host's pf.conf
make jail-publish
make jail-unpublish
```

### One trap worth repeating

`net.inet.tcp.delayed_ack=0` is worth 67 ms → 9 ms on p50. `jail-host` sets it.
If latency looks absurd on an otherwise healthy jail, check that first.

---

## 4. Deploy to Docker

```sh
make compose-doctor   # which daemon am I actually talking to, and what arch
make compose-up       # Redis + OSRM + API, sequenced, with health gates
make compose-health
make compose-logs
make compose-down
```

`compose-up` auto-builds the data image if it is missing, so step 1 is folded in
for this path.

The compose file is **not** at the repository root, so a bare `docker compose up`
will not find it. Use the targets, or:

```sh
docker compose -f deploy/docker/docker-compose.yml -p osrm-microservice ...
```

### Remote daemons

`DOCKER_HOST` targets another machine. `compose-doctor` prints the active host
and its architecture — worth running before a build, because an
architecture mismatch surfaces much later as a confusing failure.

### Linux-only observability

`process_cpu_seconds_total`, `process_resident_memory_bytes`, `process_open_fds`
and `process_start_time_seconds` need `/proc`. They appear under Docker and in
CI, and **not** in the FreeBSD jail or on a macOS dev box. Everything else on
`/metrics` is identical across all three.

---

## 5. Everything else

### Verifying a running instance

```sh
make test              # pytest suite
make lint              # ruff
cargo test --manifest-path gateway/Cargo.toml
```

Against a live gateway, the quickest confidence check is the docs surface plus
one real call:

```sh
curl -s localhost:8000/health
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/docs
curl -s localhost:8000/metrics | head
```

`/docs` (Swagger), `/redoc` and `/openapi.json` are all served.

### Load and capacity

```sh
make loadtest LOADTEST_SCENARIO=route LOADTEST_RATE=50 LOADTEST_DURATION=30
make capacity CAPACITY_ARGS="--floor-mb 300"
```

Scenarios: `route`, `nearest`, `matrix`, `matrix-graph`, `trip`, `match`,
`tile`, `vrp`, `vrp-allocate`, `health`, `metrics`, `mixed`.

`LOADTEST_URL` defaults to `http://127.0.0.1:8000` — the jail. **Pass it
explicitly for Docker.** `capacity` runs a stepped assessment with an OOM guard,
passing `--ssh`/`--jail` for itself so it can watch RSS from the host; the knobs
worth touching are `--floor-mb`, `--step-duration` and `--leak-duration`.

Cache-hit traffic is where the gateway itself dominates; `--distinct-payloads N`
cycles a fixed set instead of randomising every request, which is the regime
worth measuring separately.

### Differential parity

The harness that judged the port against the FastAPI original:

```sh
make parity-selfcheck   # validates the harness itself; offline, no engine
make parity             # diff two running gateways over a seeded corpus
make parity-record      # engine proxy that captures upstream responses
make parity-replay      # serve those fixtures instead of a real engine
```

`parity-replay` is how the test suite exercises every endpoint with no engine
and no network. Note the committed goldens were recorded from **this** gateway,
so they are a regression baseline, not a parity one.

### The Rust evaluation spike

`rust-spike/` is a two-endpoint benchmark target, not a gateway, kept so the
original comparison stays reproducible. It never starts as part of a normal
`compose-up` — it sits behind a compose profile.

```sh
make compose-spike-up   # Docker
make jail-spike-up      # jail; needs jail-bootstrap first
make spike-bench        # same scenario against both, back to back
```

### Configuration

One file — `deploy/env/app.env` — is read by both deployments. Docker loads it
via `env_file:`, and `install.sh` copies it into the jail. Real environment
variables outrank it on both paths. No secrets: it is committed.

`OSRM_BASE_URL` and `REDIS_URL` are overridden per deployment, so editing them
there has no effect. See [configuration.md](configuration.md).

---

## Known rough edges

Two things that will waste your time if you meet them cold:

- **`make help` advertises `build-pkg`, `publish` and `clean-pkg`. None of them
  exist.** They went with the PyPI package when the FastAPI implementation was
  archived; the help text was not updated. Running them fails with
  `No rule to make target`.
- **The README's "Core Services" section describes `osrm_client.py`,
  `graph_builder.py` and `vrp_service.py`**, which no longer exist. The
  equivalents are `gateway/src/osrm/client.rs`, the `build_graph` function in
  `gateway/src/handlers.rs`, and `gateway/src/vrp/`.
