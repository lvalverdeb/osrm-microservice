# Tier 3 — Operations

**How to run this system, and what to do when it misbehaves.**

Tier 1 is what the sources state about themselves; Tier 2 explains the
mechanisms; Tier 3 is the operational layer on top of both — deployment,
pipelines, gates, load, limits and failure modes.

Like Tier 2 these are authored and cite what they were read from. Unlike Tier 2
they are task-shaped: each one should be usable with a terminal open.

| Document | Read from |
|---|---|
| [32 — Deployment: Docker](32-deployment-docker.md) | `deploy/docker/`, the `compose-*` targets |
| [33 — Deployment: FreeBSD jail](33-deployment-freebsd-jail.md) | `deploy/freebsd/`, the `jail-*` targets |
| [34 — Data pipeline](34-data-pipeline.md) | the Makefile, `Dockerfile.builder`, `install.sh`, `build_sample_slice.py` |
| [35 — CI and merge gates](35-ci-and-merge-gates.md) | `.github/workflows/tests.yml` |
| [36 — The parity harness](36-parity-harness.md) | `parity/`, `tests/test_parity_*.py` |
| [37 — Load testing and capacity](37-load-testing-and-capacity.md) | `loadtest/`, `benchmarks/BASELINE.md` |
| [38 — Scaling limits and tuning](38-scaling-and-tuning.md) | `config.rs`, `app.env`, `decompose.py`, `accelerate.py` |
| [39 — Troubleshooting](39-troubleshooting.md) | the failure modes named across the whole tree |

## By task

**Standing it up for the first time:** 34 (get the routing data), then 32 or 33
depending on the host. Run the doctor target first either way.

**Something is wrong right now:** 39. It is symptom-first and every entry has a
named cause in the source.

**Deciding whether a change is safe to merge:** 35, then 36 for what the parity
gate actually proves.

**Sizing a box, or making it go faster:** 38 for the knobs and their committed
values, 37 for how to measure without taking the host down.

**Changing an application setting:** [doc 04](../tier1/04-configuration-reference.md)
for the full table — it is generated and cross-checked against the file both
deployments ship — then 32 or 33 for where that file lands.

## Two things worth knowing before you start

**The two deployments share their application settings and nothing else.**
`deploy/env/app.env` is loaded by Docker through `env_file:` and installed into
the jail by `install.sh`. Change a rate limit or a cache TTL there and both get
it. Everything else — engine URLs, Redis URL, workers, trusted proxies — is
deployment-specific and set separately in each.

**Both refuse rather than truncate.** Every limit in doc 38 produces a 422 or a
503 naming the number, not a quietly smaller answer. When tuning, that is the
property to preserve.

## Stale documentation this set found

Both `.env.example` templates still describe the retired FastAPI deployment:

- `deploy/docker/.env.example` calls `API_WORKERS` "uvicorn worker processes"
  and claims the entrypoint sets `PROMETHEUS_MULTIPROC_DIR` above 1. There is
  no entrypoint script, and workers are tokio threads in one process.
- `deploy/freebsd/.env.example` says the same of `JAIL_API_WORKERS`, and the
  Makefile's `JAIL_FORWARDED_ALLOW_IPS` comment describes uvicorn's behaviour.

The compose file and the rc.d script both carry current comments that
contradict them. Neither template is read by anything — they are copy-from
references — so the cost is a reader being misled, not a misconfiguration.

Two more remnants of the same migration:

- **The Makefile carries a six-line comment for a target that no longer
  exists** (above the parity section), describing how to refresh a FastAPI
  OpenAPI reference file. Its own last line says "When FastAPI goes, delete the
  reference file, that test, and this target" — all three were deleted;
  `tests/test_openapi_snapshot.py` is gone too. Only the comment survived.
- **The rc.d script names `osrm-api-gateway.python` as the rollback target.**
  That file is not in `deploy/freebsd/`, so there is no rollback path to the
  previous implementation.

## Gaps these documents did not fill

- **`cargo clippy` is documented as a local command but no CI step runs it.**
- **No coverage gate**, and no published coverage figure.
- **No alerting rules or dashboards** are in the repository, though the metric
  names and bucket choices are pinned to make Python-era dashboards keep working
  (doc 17).
- **No backup or restore procedure.** Redis is cache-only by design and holds
  nothing worth restoring; the routing graphs are rebuildable from the extract;
  snapshot retention is named as a deployment decision and not implemented
  (doc 29).
- **No documented rollback**, beyond the rc.d script's reference to the retired
  FastAPI implementation as the rollback target — which no longer exists in the
  tree.
