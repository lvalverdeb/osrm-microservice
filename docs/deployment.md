# Deployment

The project supports **two** deployment options, and no others. Both run the same
three services — the OSRM engine, a Redis cache, and the FastAPI gateway — and both
are driven from the `Makefile`.

| | **Docker** | **FreeBSD jail** |
|---|---|---|
| Target host | A Linux Docker host (local or remote via `DOCKER_HOST`) | A jail on a FreeBSD host |
| Files | [`deploy/docker/`](../deploy/docker) | [`deploy/freebsd/`](../deploy/freebsd) |
| Entry point | `make compose-up` | `make jail-up` |
| Services come from | Images built from three Dockerfiles | FreeBSD packages + rc.d scripts |
| OSRM data built by | `make process-osrm` (builder image, on your machine) | `make jail-data` (in the jail) |
| Reachable on | The published container port | Jail-local loopback; `make jail-publish` to expose |
| Detail | Below | [deployment_freebsd.md](deployment_freebsd.md) |

They share no state and can be used side by side. Nothing in `src/` differs
between them — `OSRM_BASE_URL` in `app/config.py` is the only thing that changes,
and it is already a setting.

**Which one?** Use Docker unless the target is a FreeBSD jail. A jail cannot run
Docker — jails share the FreeBSD kernel, and Docker needs Linux namespaces and
cgroups — which is the entire reason the second path exists.

---

## Option 1 — Docker

Uses a **local build and bundled transfer** workflow: the OSM data is processed
into an image on your machine, then that image is used as a build stage for the
runtime image. Nothing is bind-mounted, so the stack can be deployed to a remote
Docker host without shipping the data separately.

### Prerequisites

- Docker Desktop (macOS) or a Docker daemon
- `make`
- A remote Docker host if you are not deploying locally (e.g. a Linux VM at `10.211.55.28`)

#### Apple Silicon (M1/M2/M3)

The official OSRM images are published for `linux/amd64` only, so this project
relies on Docker's emulation. Docker Desktop for Mac usually enables it
automatically; elsewhere, or after an `exec format error` during build:

```bash
docker run --privileged --rm tonistiigi/binfmt --install all
```

`make compose-up` and `make process-osrm` both attempt this for you.

### 1. Data acquisition and processing

```bash
# Download the latest Costa Rica map data into ./data
make download-data

# Process it for one profile (car, bicycle, foot); defaults to car
make process-osrm PROFILE=car
```

`make process-osrm` builds the `osrm-data-builder` image from
[`deploy/docker/Dockerfile.builder`](../deploy/docker/Dockerfile.builder), which
runs `osrm-extract`, `osrm-partition` and `osrm-customize` inside the container.

### 2. Deploy

The processed data is bundled from the builder image into the OSRM runtime image
by the multi-stage
[`deploy/docker/Dockerfile.osrm`](../deploy/docker/Dockerfile.osrm).
`osrm/osrm-backend` is multi-arch, so confirm which daemon you are targeting
before starting anything.

```bash
# Target a remote host (skip for a local daemon)
export DOCKER_HOST=tcp://10.211.55.28:2375

# Show the active Docker host and its architecture
make compose-doctor

# Build and start Redis + OSRM + API with safe sequencing and health checks
# (auto-builds osrm-data-builder first)
make compose-up

make compose-logs     # tail api, osrm and redis
make compose-health   # probes /ready, which covers both gateway and engine
make compose-down     # stop and remove the services
```

Avoid `docker compose down & docker compose up --build`: the `&` backgrounds the
first command and the two race.

### Calling compose directly

`make compose-*` is the supported entry point. If you need raw compose:

```bash
docker compose -f deploy/docker/docker-compose.yml -p osrm-microservice up
```

Both flags matter. `-f` is needed because the compose file is no longer at the
repository root; `-p` pins the project name, which would otherwise be derived
from the file's directory (`docker`) and rename the default network.

The build contexts inside the compose file are `../..`, i.e. the repository root
— the API image copies `src/app` and `pyproject.toml`, and the builder image
copies `data/`. That also means `.dockerignore` stays at the repository root,
since Docker reads it from the root of the build context rather than from beside
the Dockerfile.

### What is in `deploy/docker/`

| File | Role |
|---|---|
| `Dockerfile` | The FastAPI gateway image |
| `Dockerfile.builder` | Runs extract/partition/customize, exports `/data` |
| `Dockerfile.osrm` | OSRM runtime, with the built data copied in from the builder |
| `docker-compose.yml` | The three services plus the `build`-profile data builder |

---

## Option 2 — FreeBSD jail

A jail cannot run Docker, so the same three services run natively: `osrm-backend`
and `redis` come from FreeBSD packages, and only the gateway needs an rc.d script
of our own. Every call takes the route
`ssh <JAIL_HOST>` → `deploy/freebsd/jailctl.sh` → `jexec` → `deploy/freebsd/install.sh`,
because the jail runs no sshd of its own and `jexec(8)` requires real root on the host.

```bash
make jail-doctor      # identity, arch, memory, escalation method, package state
make jail-host        # host prerequisites: kernel tunables, jail.conf loopback, jail resolver
make jail-bootstrap   # packages and the service user
make jail-data        # build OSRM data in the jail (slow, memory-hungry)
make jail-up          # deploy the gateway, start all three services, health-check
make jail-health      # probes /ready, which covers both gateway and engine
make jail-logs
make jail-down

make jail-publish     # expose the gateway on the host's IP (edits the host's pf.conf)
make jail-unpublish
```

First run is `jail-host` → `jail-bootstrap` → `jail-data` → `jail-up`.

`jail-host` is not optional. Besides the jail-local loopback and resolver, it
applies the kernel tunables this deployment depends on — without them the jail
serves ~7x the latency and simply looks like slower hardware. `make jail-doctor`
reports whether they are applied and persisted.

Full detail — host setup, the loopback and `pf.conf` story, why the stock
`osrm-backend` rc.d script cannot be used, and troubleshooting — is in
[deployment_freebsd.md](deployment_freebsd.md).

## Scaling

Both options default to **one uvicorn worker** and trust no proxy, which is the
safe starting point. Two knobs change that; they are deployment knobs, so they
live in each option's `.env.example`, not in the shared `deploy/env/app.env`.

| | Docker | FreeBSD jail |
|---|---|---|
| Worker processes | `API_WORKERS` | `JAIL_API_WORKERS` → `osrm_api_gateway_workers` |
| Trusted proxies | `FORWARDED_ALLOW_IPS` | `JAIL_FORWARDED_ALLOW_IPS` → `osrm_api_gateway_forwarded_allow_ips` |

```bash
make compose-up API_WORKERS=4 FORWARDED_ALLOW_IPS=10.0.0.0/8
make jail-up    JAIL_API_WORKERS=2 JAIL_FORWARDED_ALLOW_IPS=10.0.0.0/8
```

### Workers

Above one worker, both paths also set `PROMETHEUS_MULTIPROC_DIR` and wipe that
directory at start. Without it `prometheus_client` counts in process memory and a
scrape reports roughly 1/N of the traffic — plausible-looking numbers that are
silently wrong; stale files from a previous run are double-counted for the same
reason. `deploy/docker/entrypoint.sh` and `deploy/freebsd/osrm-api-gateway`
implement the same rules, so do not add `--workers` by overriding `command:`.

Each worker carries its own L1 cache and httpx pool, so memory grows with the
count. On a memory-bound host, more workers can cost more than they return.

### Trusted proxies

uvicorn installs `ProxyHeadersMiddleware` by default but trusts only
`127.0.0.1`. Behind a reverse proxy or load balancer the gateway therefore sees
the *proxy's* address as the client, and since rate limits are keyed per client
address, **every client collapses into one shared bucket**. Naming the proxies
restores per-client limits.

uvicorn walks `X-Forwarded-For` from the right and takes the first hop outside
the trusted set, so a client cannot claim an identity by prepending one.

> **Never set this to `*`.** uvicorn then trusts every hop and takes the leftmost
> `X-Forwarded-For` entry, which the client controls. That does not weaken rate
> limiting, it defeats it: any caller mints a fresh bucket per request. Both
> deployments warn at startup if you do.

Plain NAT and L4 forwarding do not need this — they preserve the source address.
It is L7 proxies, which terminate the connection and open a new one, that require it.

## Load testing either option

`make loadtest` drives a **running** gateway, so it needs to be told which one.
`LOADTEST_URL` defaults to `http://127.0.0.1:8000`, which is the jail: `make
jail-publish` redirects the host's `JAIL_API_PORT` (8000). The Docker path
publishes `API_PORT` (8080) on the Docker host, so it needs the URL explicitly:

```bash
# Docker
make loadtest LOADTEST_URL=http://<docker-host>:8080

# FreeBSD jail, after make jail-publish
make loadtest
```

A single-source run cannot exceed one rate-limit bucket, so it measures the
limiter rather than the service. `--forwarded-for-pool N` spreads the load over N
synthetic client addresses (from the RFC 2544 benchmarking range) so the run is
bounded by the server instead — it needs the gateway started with a
`FORWARDED_ALLOW_IPS` that covers the generator's address:

```bash
make loadtest LOADTEST_URL=http://<host>:8080 LOADTEST_ARGS="--forwarded-for-pool 200"
```

`LOADTEST_SCENARIO` (`route` by default, or `mixed`), `LOADTEST_RATE` and
`LOADTEST_DURATION` tune the run; `make capacity` does the full assessment and is
jail-specific, since its `--ssh` memory probe watches the FreeBSD host.

A run that reports **100% `transport-error` with ~2ms latencies on every
endpoint**, `/health` and `/metrics` included, is pointed at nothing. That is
connection-refused, not a service under stress — check the URL and the published
port before reading it as a failure.

## Configuration

App settings are defined once and loaded by both options; deployment knobs are kept
per option.

| File | Holds | Read by |
|---|---|---|
| `deploy/env/app.env` | all 29 app settings | **both** — Docker via `env_file:`, the jail by installing it to `${JAIL_DIR}/.env` |
| `deploy/docker/.env.example` | `DOCKER_HOST`, `API_PORT`, `OSRM_PORT`, `PROFILE`, `OSM_FILE` | template — copy what you need into the root `.env` |
| `deploy/freebsd/.env.example` | `JAIL_HOST`, `JAIL_NAME`, `JAIL_DIR`, `JAIL_API_*`, `GEO_URL`, … | template — same |
| `.env` (repo root, gitignored) | local tooling; the values you actually set | the `Makefile`, via `-include .env` + `export` |

`deploy/env/app.env` is committed and holds no secrets — the real process
environment outranks it on both paths, so anything sensitive goes there instead.

Each deployment overrides exactly two settings, `OSRM_BASE_URL` and `REDIS_URL`,
because only it knows where its engine and cache live. Everything else is shared,
so changing a rate limit or cache TTL in `deploy/env/app.env` changes both
deployments. Full precedence rules are in [configuration.md](configuration.md).

## Keeping the two in step

The jail path deliberately mirrors the Docker path — `make jail-data` follows
`Dockerfile.builder` step for step, and the `osrm-routed` rc.d script uses the same
engine flags as the `osrm` service in `docker-compose.yml`. When you change engine
flags or resource limits, change them in **both** places in the same commit.