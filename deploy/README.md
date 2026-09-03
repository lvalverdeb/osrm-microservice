# deploy/

Everything needed to run this service, for the two supported deployment options.
Pick one:

| Option | Directory | Start with | When |
|---|---|---|---|
| **Docker** | [`docker/`](docker) | `make compose-up` | Any Linux Docker host, local or remote |
| **FreeBSD jail** | [`freebsd/`](freebsd) | `make jail-up` | A jail on a FreeBSD host, which cannot run Docker |

[`env/app.env`](env/app.env) is shared by both: it holds every app setting, and each
option loads it at runtime — Docker through `env_file:`, the jail by installing it to
`${JAIL_DIR}/.env`. Change a rate limit or cache TTL there and both deployments get
it. Deployment-specific knobs stay in each option's own `.env.example`.

Both run the same three services — OSRM engine, Redis cache, and the Rust
gateway — and share no state. Nothing in `gateway/` differs between them: the
Docker image and the jail build the same source, one with `cargo build
--release` in a builder stage, the other natively in the jail.

Full instructions: [`docs/deployment.md`](../docs/deployment.md). Jail-specific
detail: [`docs/deployment_freebsd.md`](../docs/deployment_freebsd.md).

## docker/

| File | Role |
|---|---|
| `Dockerfile` | The gateway image: builds `gateway/` with cargo, then copies the release binary into a slim runtime |
| `Dockerfile.builder` | Runs `osrm-extract` / `-partition` / `-customize`, exports `/data` |
| `Dockerfile.osrm` | OSRM runtime, with the built data copied in from the builder |
| `Dockerfile.spike` | The `rust-spike/` evaluation binary — a benchmark target, not the gateway |
| `docker-compose.yml` | The three services, plus the data builder under the `build` profile |

Build contexts are the repository root, so `.dockerignore` lives there and not
here — Docker reads it from the root of the build context.

`.env.example` here lists Docker-only knobs (`DOCKER_HOST`, `API_PORT`, `OSRM_PORT`,
`PROFILE`, `OSM_FILE`); it is a template, not a file anything reads.

## freebsd/

| File | Role |
|---|---|
| `jailctl.sh` | Runs on the FreeBSD **host**: applies host kernel tunables, stages sources, then `jexec`s into the jail |
| `install.sh` | Runs **inside** the jail: phased, idempotent installer |
| `osrm-api-gateway` | rc.d script for the gateway |
| `osrm-gateway-spike` | rc.d script for the `rust-spike/` benchmark target |
| `osrm-routed` | rc.d script for the OSRM engine |
| `redis-cache.conf` | Redis configuration for the cache |

`.env.example` here lists jail-only knobs (`JAIL_HOST`, `JAIL_NAME`, `JAIL_API_*`,
`GEO_URL`, …); like the Docker one it is a template, not a file anything reads.

These are uploaded by `make jail-stage` together with `env/`, which every `jail-*`
target depends on.