# Include .env file for environment variables (like UV_PUBLISH_TOKEN)
-include .env.publish
export

# Use local data directory for processing
DATA_DIR = $(shell pwd)/data

OSM_FILE = costa-rica-latest.osm.pbf
OSM_BASE = $(OSM_FILE:.osm.pbf=)
GEO_URL = http://download.geofabrik.de/central-america/$(OSM_FILE)

# Profile selection (car, bicycle, foot) - defaults to car
PROFILE ?= car

# Both deployment paths live under deploy/. -p is mandatory now that the compose
# file is not at the repository root: without it the project name would default
# to the file's directory ("docker") and rename the default network.
COMPOSE_FILE ?= deploy/docker/docker-compose.yml
COMPOSE ?= docker compose -f $(COMPOSE_FILE) -p osrm-microservice

.PHONY: help download-data process-osrm compose-doctor compose-up compose-down compose-logs compose-health clean build-pkg publish clean-pkg test lint spaghetti fenceline loadtest capacity jail-doctor jail-host jail-stage jail-bootstrap jail-data jail-up jail-down jail-logs jail-health jail-publish jail-unpublish openapi-snapshot parity parity-record parity-replay parity-selfcheck compose-spike-up compose-spike-down compose-spike-logs compose-spike-health jail-spike-up jail-spike-down jail-spike-logs jail-spike-health spike-bench

help:
	@echo "Two deployment options, see docs/deployment.md:"
	@echo "  Docker        -> compose-* targets, files in deploy/docker/"
	@echo "  FreeBSD jail  -> jail-* targets, files in deploy/freebsd/"
	@echo ""
	@echo "Available targets:"
	@echo "  download-data  - Download Costa Rica OSM data from Geofabrik"
	@echo ""
	@echo "  Docker deployment:"
	@echo "  process-osrm   - Extract, partition, and customize OSRM data (PROFILE=$(PROFILE))"
	@echo "  compose-doctor - Show active Docker host and daemon architecture"
	@echo "  compose-up     - Auto-build OSRM data image, then start Redis + OSRM + API with safe sequencing"
	@echo "  compose-down   - Stop and remove running compose services"
	@echo "  compose-logs   - Tail API, OSRM, and Redis logs"
	@echo "  compose-health - Quick runtime checks for API and OSRM services"
	@echo ""
	@echo "  FreeBSD jail deployment (native; a jail cannot run Docker):"
	@echo "  jail-stage     - Upload sources, deploy/freebsd and deploy/env to the host (implied by every jail-* target)"
	@echo "  jail-doctor    - Show jail identity, arch, memory and escalation method"
	@echo "  jail-host      - Apply host-level prerequisites (kernel tunables, jail.conf loopback, jail resolver)"
	@echo "  jail-bootstrap - Install packages and create the service user in the jail"
	@echo "  jail-data      - Build OSRM data in the jail (PROFILE=$(PROFILE))"
	@echo "  jail-up        - Deploy the gateway and (re)start all three services"
	@echo "  jail-down      - Stop gateway, OSRM and Redis in the jail"
	@echo "  jail-logs      - Tail the gateway log and system messages"
	@echo "  jail-health    - Quick runtime checks for API and OSRM in the jail"
	@echo "  jail-publish   - Redirect the host's port $(JAIL_API_PORT) to the jail's gateway (edits the host's pf.conf)"
	@echo "  jail-unpublish - Remove that redirect from the host's pf.conf"
	@echo ""
	@echo "  build-pkg      - Build the Python package for PyPI distribution"
	@echo "  publish        - Publish the Python package to PyPI (requires UV_PUBLISH_TOKEN in .env)"
	@echo "  clean          - Remove downloaded and processed data"
	@echo "  clean-pkg      - Remove Python build artifacts"
	@echo "  test           - Run the pytest suite"
	@echo "  lint           - Run ruff checks"
	@echo "  spaghetti      - Run the spaghetti-detector complexity/architecture scan"
	@echo "  fenceline      - Run the fenceline vulnerability scan (fails on high severity or above)"
	@echo "  loadtest       - Load-test a running gateway (LOADTEST_URL/SCENARIO/RATE/DURATION)"
	@echo "                   LOADTEST_URL defaults to $(LOADTEST_URL) (the jail); pass it for Docker"
	@echo "  capacity       - Full capacity assessment with an OOM guard (LOADTEST_URL, CAPACITY_ARGS)"
	@echo ""
	@echo "  Rust evaluation spike (rust-spike/) -- a benchmark target, not a gateway:"
	@echo "  compose-spike-up/-down/-logs/-health - Run the spike beside the Docker api service"
	@echo "  jail-spike-up/-down/-logs/-health    - Build and run it in the jail (needs jail-bootstrap)"
	@echo "  spike-bench    - Same loadtest scenario against both gateways (LOADTEST_URL, SPIKE_URL)"
	@echo ""
	@echo "  Differential parity (parity/):"
	@echo "  parity           - Diff both gateways' responses over a seeded corpus"
	@echo "  parity-selfcheck - Validate the harness itself; offline, no engine needed"
	@echo "  parity-record    - Engine proxy that records upstream responses to fixtures"
	@echo "  parity-replay    - Serve recorded fixtures instead of a real engine"

download-data:
	mkdir -p $(DATA_DIR)
	curl -L $(GEO_URL) -o $(DATA_DIR)/$(OSM_FILE)

process-osrm:
	@echo "Ensuring cross-platform emulation is available on Docker daemon..."
	-docker run --privileged --rm tonistiigi/binfmt --install all
	@echo "Building OSRM data builder for profile: $(PROFILE)..."
	docker build --pull -t osrm-data-builder --build-arg PROFILE=$(PROFILE) -f deploy/docker/Dockerfile.builder .
	@echo "Local OSRM builder image ready."

compose-doctor:
	@echo "DOCKER_HOST=$${DOCKER_HOST:-<not set>}"
	@docker info --format 'Daemon: {{.OSType}}/{{.Architecture}}'

compose-up:
	$(MAKE) compose-doctor
	@echo "Ensuring cross-platform emulation is available on Docker daemon..."
	-docker run --privileged --rm tonistiigi/binfmt --install all
	$(COMPOSE) build osrm-data-builder
	$(COMPOSE) up -d redis
	$(COMPOSE) up -d --build osrm
	$(COMPOSE) up -d --build api
	$(MAKE) compose-health

compose-down:
	$(COMPOSE) down

compose-logs:
	$(COMPOSE) logs --tail=100 api osrm redis

compose-health:
	$(COMPOSE) ps
	@i=0; until $(COMPOSE) exec -T api curl -fsS http://localhost:8000/ready >/dev/null; do \
		i=$$((i+1)); \
		if [ $$i -ge 30 ]; then echo "Readiness check failed after 30 attempts (gateway or OSRM engine down)"; exit 1; fi; \
		sleep 1; \
	done
	@echo "Compose health checks passed."

# --- Rust evaluation spike (rust-spike/) -----------------------------------
# A benchmark target, not a second gateway: two of eleven endpoints, no rate
# limiting, metrics, tracing, retry or L2 cache. Both paths run it beside the
# Python gateway against the same engine, so the head-to-head in
# rust-spike/README.md can be reproduced on real hardware.

COMPOSE_SPIKE = $(COMPOSE) --profile spike

compose-spike-up:
	$(COMPOSE_SPIKE) up -d --build spike
	$(MAKE) compose-spike-health

compose-spike-down:
	$(COMPOSE_SPIKE) rm -sf spike

compose-spike-logs:
	$(COMPOSE_SPIKE) logs --tail=100 spike

# Probes from inside the container, like compose-health: the spike's own
# HEALTHCHECK runs there too, and this avoids depending on published ports.
compose-spike-health:
	$(COMPOSE_SPIKE) ps spike
	@i=0; until $(COMPOSE_SPIKE) exec -T spike curl -fsS http://localhost:8001/ready >/dev/null; do \
		i=$$((i+1)); \
		if [ $$i -ge 30 ]; then echo "Spike readiness failed after 30 attempts (spike or OSRM engine down)"; exit 1; fi; \
		sleep 1; \
	done
	@echo "Spike health checks passed."

jail-spike-up: jail-stage
	$(JAIL_SSH) '$(JAILCTL) spike'

jail-spike-down: jail-stage
	$(JAIL_SSH) '$(JAILCTL) spike-stop'

jail-spike-logs: jail-stage
	@$(JAIL_SSH) 'tail -n 100 /var/log/osrm-gateway-spike.log' || \
		echo "no spike log yet; run 'make jail-spike-up' first"

jail-spike-health:
	@curl -fsS $(SPIKE_URL)/ready >/dev/null && echo "spike ready at $(SPIKE_URL)" || \
		{ echo "spike not reachable at $(SPIKE_URL)"; exit 1; }

# Head-to-head: the same scenario against both gateways, back to back. Keep
# worker counts equal on the two sides or this measures the configuration.
spike-bench:
	@echo "=== Python gateway: $(LOADTEST_URL) ==="
	uv run python -m loadtest.run --url $(LOADTEST_URL) \
		--scenario $(LOADTEST_SCENARIO) --rate $(LOADTEST_RATE) \
		--duration $(LOADTEST_DURATION) $(LOADTEST_ARGS)
	@echo ""
	@echo "=== Rust spike: $(SPIKE_URL) ==="
	uv run python -m loadtest.run --url $(SPIKE_URL) \
		--scenario $(LOADTEST_SCENARIO) --rate $(LOADTEST_RATE) \
		--duration $(LOADTEST_DURATION) $(LOADTEST_ARGS)

# The Rust gateway serves FastAPI's generated OpenAPI schema rather than
# re-describing every model in utoipa annotations: the pydantic models stay the
# single source of truth, and there is no third description to drift. The binary
# embeds this file at build time, so regenerate it whenever the models change --
# tests/test_openapi_snapshot.py fails if you forget.
openapi-snapshot:
	uv run python -c "import json, sys; sys.path.insert(0, 'src'); \
		from app.main import app; \
		json.dump(app.openapi(), open('gateway/openapi.json', 'w'), indent=2, sort_keys=True)"
	@echo "wrote gateway/openapi.json"

# --- Differential parity (parity/) ------------------------------------------
# Replays a seeded corpus against both gateways and diffs the responses, with
# per-endpoint tolerance: exact for /matrix and /tile, float-tolerant for the
# pass-throughs, quality-based for /vrp. Both gateways MUST point at the same
# osrm-routed or this measures the map data instead of the port.
#
# Exit codes: 0 clean, 1 the candidate diverged, 2 the run was misconfigured
# (rate-limited, half-down, unreachable) -- a distinction worth keeping, since
# a harness that cries wolf stops being run.
PARITY_REFERENCE ?= $(LOADTEST_URL)
PARITY_CANDIDATE ?= $(SPIKE_URL)
PARITY_SEED      ?= 20260822
PARITY_CASES     ?= 10
PARITY_ARGS      ?=

parity:
	uv run python -m parity --reference $(PARITY_REFERENCE) \
		--candidate $(PARITY_CANDIDATE) --seed $(PARITY_SEED) \
		--cases $(PARITY_CASES) --report-json parity-report.json \
		--report-dir parity-diffs $(PARITY_ARGS)

# Record upstream responses once, replay them forever. Start one of these, point
# a gateway's OSRM_BASE_URL at it, and run `make parity` as usual.
#
#   parity-record   forwards to a real engine and saves what it returns
#   parity-replay   serves only what was recorded; anything else gets a 404
#                   naming the URL, which is how a gateway that builds a
#                   different upstream request gets caught
PARITY_ENGINE      ?= http://127.0.0.1:5000
PARITY_ENGINE_PORT ?= 5599

parity-record:
	uv run python -m parity.engine --mode record --engine $(PARITY_ENGINE) \
		--port $(PARITY_ENGINE_PORT)

parity-replay:
	uv run python -m parity.engine --mode replay --port $(PARITY_ENGINE_PORT)

# The harness's own acceptance test: the Python gateway against itself, in
# process, engine stubbed. It must come back clean -- a dirty self-diff means
# the harness is broken, not the port. Runs offline, so CI already covers it.
parity-selfcheck:
	uv run python -m pytest tests/test_parity_selfdiff.py tests/test_parity_compare.py \
		tests/test_parity_corpus.py tests/test_parity_quality.py -q

build-pkg:
	@echo "Building osrm-api-gateway package..."
	uv build

publish: build-pkg
	@echo "Publishing to PyPI..."
	@if [ -z "$$UV_PUBLISH_TOKEN" ]; then \
		echo "Error: UV_PUBLISH_TOKEN is not set in .env file or environment variables."; \
		exit 1; \
	fi
	uv publish --token $$UV_PUBLISH_TOKEN

clean:
	rm -rf $(DATA_DIR)

clean-pkg:
	rm -rf dist/

test:
	uv run python -m pytest tests/ -q --tb=short

lint:
	uv run ruff check .

spaghetti:
	spaghetti --package osrm-api-gateway=src/app --severity warning

# --baseline suppresses only the fingerprints listed in the file, each matched on
# [cwe_id, file, exact code line]. A suppression therefore stops applying as soon
# as that line changes, and everything else still reports. The justification for
# each entry is in the file's _comment block.
FENCELINE_BASELINE ?= .fenceline-baseline.json

fenceline:
	fenceline --package osrm-api-gateway=src/app --baseline $(FENCELINE_BASELINE)

# --- load testing ------------------------------------------------------------
# Open-model generator: requests are launched on a fixed schedule, so a slow
# server shows up as latency rather than a quietly reduced rate. Payloads are
# randomised, which also keeps the L1/Redis caches from answering everything.
#
# The default targets the *jail* deployment: `make jail-publish` redirects the
# host's JAIL_API_PORT (8000). The Docker path publishes API_PORT (8080) on the
# Docker host instead, so it needs the URL passed explicitly:
#
#   make loadtest LOADTEST_URL=http://<docker-host>:8080
#
# A run that reports 100% transport-error with ~2ms latencies on every endpoint,
# /health and /metrics included, is pointed at nothing -- check the URL before
# reading it as a failing service.
LOADTEST_URL      ?= http://127.0.0.1:8000
LOADTEST_SCENARIO ?= route
LOADTEST_RATE     ?= 25
LOADTEST_DURATION ?= 30
LOADTEST_ARGS     ?=
# The spike's URL, used by spike-bench and jail-spike-health. Mirrors
# LOADTEST_URL's assumption -- the jail deployment reached through the host --
# but note jail-publish only redirects JAIL_API_PORT, so reaching the spike from
# outside needs its port forwarded too, or the bench run from the jail host:
#
#   make spike-bench SPIKE_URL=http://127.0.0.1:8081   # Docker path
SPIKE_URL         ?= http://127.0.0.1:$(JAIL_SPIKE_PORT)

loadtest:
	uv run python -m loadtest.run --url $(LOADTEST_URL) \
		--scenario $(LOADTEST_SCENARIO) --rate $(LOADTEST_RATE) \
		--duration $(LOADTEST_DURATION) $(LOADTEST_ARGS)

# Full assessment: endpoint smoke, leak check, rate ramp, payload ladders. The
# --ssh probe watches host memory and aborts a phase before the OOM killer gets
# involved -- this host is shared with the db and dev jails.
CAPACITY_ARGS ?=

capacity:
	uv run python -m loadtest.capacity --url $(LOADTEST_URL) \
		--ssh $(JAIL_HOST) --jail $(JAIL_NAME) $(CAPACITY_ARGS)


# --- FreeBSD jail deployment -------------------------------------------------
# A FreeBSD jail cannot run Docker, so this path runs the same three services
# natively: osrm-backend and redis come from FreeBSD packages and use the rc.d
# scripts their own ports ship, and only the gateway needs one of ours. The
# compose-* targets above are untouched -- the two paths are independent.
#
# The api jail runs no sshd of its own (the host's sshd binds *:22 and answers
# for the jail's address too), so every call takes the route
#     ssh <host> -> jailctl.sh -> jexec -> install.sh inside the jail
# jexec(8) requires real root on the host; `make jail-doctor` reports whether
# this login has it and how to fix it if not.

JAIL_HOST      ?= developer@10.211.55.33
JAIL_NAME      ?= api
HOST_STAGE     ?= /tmp/osrm-api-gateway-stage
JAIL_STAGE     ?= /tmp/osrm-api-gateway-stage
JAIL_DIR       ?= /usr/local/www/osrm-api-gateway
JAIL_DATA_DIR  ?= /var/db/osrm-backend
JAIL_APP_USER  ?= osrmapi
JAIL_API_WORKERS ?= 1
# Trusted proxy addresses/CIDRs for uvicorn. Empty keeps its 127.0.0.1-only
# default, so X-Forwarded-For is ignored and the limiter keys on the peer.
JAIL_FORWARDED_ALLOW_IPS ?=
JAIL_API_HOST  ?= 0.0.0.0
JAIL_API_PORT  ?= 8000
# Spike port, forwarded across the jail boundary by jailctl.sh's jail_env.
JAIL_SPIKE_PORT ?= 8001
JAIL_REDIS_URL ?= redis://127.0.0.1:6379/0
JAIL_OSRM_URL  ?= http://127.0.0.1:5000
JAIL_SSH_OPTS  ?= -o StrictHostKeyChecking=accept-new

JAIL_SSH = ssh $(JAIL_SSH_OPTS) $(JAIL_HOST)

# Deliberately JAIL_-prefixed: `-include .env` + `export` at the top of this
# file would otherwise leak a developer OSRM_BASE_URL into the jail.
JAIL_ENV = JAIL_NAME=$(JAIL_NAME) HOST_STAGE=$(HOST_STAGE) JAIL_STAGE=$(JAIL_STAGE) \
	JAIL_DIR=$(JAIL_DIR) JAIL_DATA_DIR=$(JAIL_DATA_DIR) \
	JAIL_APP_USER=$(JAIL_APP_USER) JAIL_API_HOST=$(JAIL_API_HOST) \
	JAIL_API_PORT=$(JAIL_API_PORT) JAIL_API_WORKERS=$(JAIL_API_WORKERS) \
	JAIL_FORWARDED_ALLOW_IPS='$(JAIL_FORWARDED_ALLOW_IPS)' JAIL_REDIS_URL=$(JAIL_REDIS_URL) \
	JAIL_OSRM_URL=$(JAIL_OSRM_URL) PROFILE=$(PROFILE) OSM_FILE=$(OSM_FILE) \
	OSM_BASE=$(OSM_BASE) GEO_URL=$(GEO_URL)

JAILCTL = $(JAIL_ENV) sh $(HOST_STAGE)/deploy/freebsd/jailctl.sh

jail-stage:
	@echo "Staging sources to $(JAIL_HOST):$(HOST_STAGE)"
	@$(JAIL_SSH) 'rm -rf $(HOST_STAGE)/src $(HOST_STAGE)/deploy $(HOST_STAGE)/rust-spike && \
		mkdir -p $(HOST_STAGE)/data'
	@tar -cf - src/app pyproject.toml deploy/freebsd deploy/env README.md \
		rust-spike/Cargo.toml rust-spike/Cargo.lock rust-spike/src | \
		$(JAIL_SSH) 'tar -xf - -C $(HOST_STAGE)'
	@if [ ! -f $(DATA_DIR)/$(OSM_FILE) ]; then \
		echo "No local $(OSM_FILE); the jail will fetch it from Geofabrik"; \
	elif $(JAIL_SSH) 'test -f $(HOST_STAGE)/data/$(OSM_FILE)'; then \
		echo "$(OSM_FILE) already staged on the host"; \
	else \
		echo "Uploading $(OSM_FILE) (saves a re-download inside the jail)"; \
		tar -cf - -C $(DATA_DIR) $(OSM_FILE) | \
			$(JAIL_SSH) 'tar -xf - -C $(HOST_STAGE)/data'; \
	fi

jail-doctor: jail-stage
	@$(JAIL_SSH) '$(JAILCTL) doctor'

# Host-level prerequisites: a jail-local loopback in jail.conf and a resolver
# that does not point at one the jail cannot reach. Idempotent; restarts the
# jail only when the address is missing.
jail-host: jail-stage
	$(JAIL_SSH) '$(JAILCTL) host'

jail-bootstrap: jail-stage
	$(JAIL_SSH) '$(JAILCTL) deps'

jail-data: jail-stage
	$(JAIL_SSH) '$(JAILCTL) data'

jail-up: jail-stage
	$(JAIL_SSH) '$(JAILCTL) sync'
	$(JAIL_SSH) '$(JAILCTL) app'
	$(JAIL_SSH) '$(JAILCTL) services'
	$(MAKE) jail-health

jail-down: jail-stage
	$(JAIL_SSH) '$(JAILCTL) stop'

jail-health: jail-stage
	@$(JAIL_SSH) '$(JAILCTL) health'

jail-logs: jail-stage
	@$(JAIL_SSH) '$(JAILCTL) logs'

# Host state, not jail state: deliberately not part of jail-up. See
# docs/deployment_freebsd.md, "Reaching the gateway from outside the host".
jail-publish: jail-stage
	$(JAIL_SSH) '$(JAILCTL) publish'

jail-unpublish: jail-stage
	$(JAIL_SSH) '$(JAILCTL) unpublish'
