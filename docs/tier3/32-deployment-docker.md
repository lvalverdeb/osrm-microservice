# 32 — Deployment: Docker

*Tier 3: authored from `deploy/docker/`, `deploy/README.md`, the `compose-*`
make targets.*

One of two supported options. Use this on **any Linux Docker host, local or
remote**; use the FreeBSD jail (doc 33) where Docker cannot run.

## What runs

| Service | Container | Image | Host port |
|---|---|---|---|
| gateway | `osrm-api-gateway` | built from `deploy/docker/Dockerfile` | `${API_PORT:-8080}` → 8000 |
| driving engine | `osrm-backend` | osrm-backend + car graph | `${OSRM_PORT:-5000}` → 5000 |
| cycling engine | `osrm-backend-cycling` | osrm-backend + bicycle graph | `${OSRM_CYCLING_PORT:-5001}` → 5000 |
| walking engine | `osrm-backend-walking` | osrm-backend + foot graph | `${OSRM_WALKING_PORT:-5002}` → 5000 |
| cache | `osrm-cache` | `redis:7-alpine` | not published |

Three engines, not one, because one `osrm-routed` serves one built graph. The
gateway addresses them by service name; **the published ports are for humans
and probes**.

The `spike` service is behind a profile and never starts with `make
compose-up`. It is a two-endpoint benchmark target, not a second gateway.

## Running it

```sh
make compose-up       # doctor, build the three graphs, start in dependency order, wait for /ready
make compose-health   # ps, then poll /ready up to 30 times
make compose-logs     # last 100 lines of api, osrm, redis
make compose-down
```

**Use the targets.** The compose file is not at the repository root, so a bare
`docker compose up` finds nothing. Invoking the CLI directly works but needs
both flags:

```sh
docker compose -f deploy/docker/docker-compose.yml -p osrm-microservice up
```

`-p` is not optional: without it the project name defaults to the file's
directory (`docker`) and the default network is renamed.

`compose-up` sequences deliberately — builders first, then redis, then the
three engines, then the api — because the api's `depends_on` requires all four
to be **healthy**, not merely started.

## Configuration, in two tiers

**Tier 1, shared with the jail:** `deploy/env/app.env`, loaded via `env_file:`.
It carries 43 keys and changing one changes both deployments. See
[doc 04](../tier1/04-configuration-reference.md).

Two details that reading the file will not tell you. Four gateway settings are
**not** in it because only a deployment can know them — `HOST`, `PORT`,
`WORKERS` and `FORWARDED_ALLOW_IPS`. And three keys in it are **read by
nothing**, listed with their reasons in `config.rs`'s `INTENTIONALLY_UNUSED`:
`OSRM_API_URL` (a client-side setting the examples read), `REDIS_MAXSIZE`
(Redis evicts server-side via `maxmemory-policy`) and `VRP_HYSTERESIS_M`
(shadowed by `VrpRequest.hysteresis_m`, which carries its own default).

**Tier 2, deployment-specific:** the `environment:` block, which outranks
`env_file:`. It sets what only this deployment can know:

```
OSRM_BASE_URL    = http://osrm-backend:5000
OSRM_URL_DRIVING = http://osrm-backend:5000
OSRM_URL_CYCLING = http://osrm-backend-cycling:5000
OSRM_URL_WALKING = http://osrm-backend-walking:5000
REDIS_URL        = redis://osrm-cache:6379/0
WORKERS          = ${API_WORKERS:-1}
FORWARDED_ALLOW_IPS = ${FORWARDED_ALLOW_IPS:-}
```

Without the three `OSRM_URL_*` values the gateway refuses cycling and walking
by name rather than answering them from the car graph — which is what it did
before they existed.

`deploy/docker/.env.example` lists the Docker-only knobs (`DOCKER_HOST`,
`API_PORT`, `OSRM_PORT`, `PROFILE`, `OSM_FILE`, `API_WORKERS`,
`FORWARDED_ALLOW_IPS`). **Nothing reads it.** Copy the lines you need into
`.env` at the repository root, which the Makefile pulls in with `-include .env`
+ `export`.

## The images

**`Dockerfile`** — two stages. The builder copies `gateway/Cargo.toml`,
`Cargo.lock` and `src`, then `cargo build --release --locked`; the runtime is
`debian:bookworm-slim` with just the binary and `curl`.

Three details are load-bearing:

- **`--locked`**, so an image builds the exact dependency versions that were
  tested rather than resolving afresh.
- **The healthcheck probes `/ready`, not `/health`.** `curl -f` only fails on a
  non-2xx status, and `/health` answers 200 even when the engine is down — so
  the container would always report healthy.
- **`CMD` is exec form with no entrypoint script**, so the binary is PID 1 and
  receives SIGTERM from `docker stop` directly. There is nothing to arrange
  before starting: `WORKERS` is tokio threads in one process, and the binary
  reads `FORWARDED_ALLOW_IPS` itself.

**`Dockerfile.builder`** runs `osrm-extract` → `-partition` → `-customize` for
one profile and exports `/data`. It is tagged per profile
(`osrm-data-builder:car|bicycle|foot`).

**`Dockerfile.osrm`** copies `/data` from **whichever tag it is told to** via
`DATA_IMAGE`. Hardcoding it "would make all three services serve the car graph
and the profile in a request would go on meaning nothing".

Build contexts are the **repository root** for every image, which is why
`.dockerignore` lives there — Docker reads it from the root of the build
context, not next to the Dockerfile.

## Platform is pinned to arm64

The three OSRM services and both data Dockerfiles declare
`platform: linux/arm64` / `--platform=linux/arm64`. `compose-up` and
`process-osrm` therefore install binfmt emulation first:

```sh
docker run --privileged --rm tonistiigi/binfmt --install all
```

That step is prefixed with `-` in the Makefile, so it is allowed to fail
(already installed, or no permission) without stopping the build. On an amd64
host, expect emulation — the extract step is the slow part of a cold start,
and it runs once per profile over the same `.pbf`.

## Remote daemons

`DOCKER_HOST=ssh://user@host` targets a remote daemon; unset it for the local
one. The daemon is reached **over SSH** — it publishes no TCP socket, so a
`tcp://host:2375` value fails with "Cannot connect to the Docker daemon".

`make compose-doctor` reports the active Docker host and the daemon's
architecture. Run it first when anything is confusing; `compose-up` runs it for
you.

## Cold start

1. `make download-data` — fetch the `.osm.pbf` from Geofabrik into `data/`.
2. `make compose-up` — builds three graphs, then starts everything.

Expect the first run to be long: three `osrm-extract` passes over the same
extract, under emulation if the host is not arm64.

## Redis is configured as a cache, not a store

`redis-server --save "" --appendonly no`. No snapshots and no AOF: this is a
cache, and persistence buys nothing.

## Known staleness

`deploy/docker/.env.example` still describes `API_WORKERS` as "uvicorn worker
processes" and claims that above 1 "the entrypoint also sets
`PROMETHEUS_MULTIPROC_DIR`". Both are Python-era statements. The compose file's
own comment is current and contradicts it: workers are tokio threads inside one
process sharing one metrics registry, and there is no entrypoint script at all.
Read the compose file, not the template.
