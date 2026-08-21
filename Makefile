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

.PHONY: help download-data process-osrm compose-doctor compose-up compose-down compose-logs compose-health clean build-pkg publish clean-pkg test lint spaghetti fenceline loadtest capacity jail-doctor jail-host jail-stage jail-bootstrap jail-data jail-up jail-down jail-logs jail-health jail-publish jail-unpublish

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
	@$(JAIL_SSH) 'rm -rf $(HOST_STAGE)/src $(HOST_STAGE)/deploy && \
		mkdir -p $(HOST_STAGE)/data'
	@tar -cf - src/app pyproject.toml deploy/freebsd deploy/env README.md | \
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
