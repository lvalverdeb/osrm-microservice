# Include .env file for environment variables (like UV_PUBLISH_TOKEN)
-include .env
export

# Use local data directory for processing
DATA_DIR = $(shell pwd)/data

OSM_FILE = costa-rica-latest.osm.pbf
GEO_URL = http://download.geofabrik.de/central-america/$(OSM_FILE)

# Profile selection (car, bicycle, foot) - defaults to car
PROFILE ?= car
COMPOSE ?= docker compose

.PHONY: help download-data process-osrm compose-doctor compose-up compose-down compose-logs compose-health clean build-pkg publish clean-pkg

help:
	@echo "Available targets:"
	@echo "  download-data  - Download Costa Rica OSM data from Geofabrik"
	@echo "  process-osrm   - Extract, partition, and customize OSRM data (PROFILE=$(PROFILE))"
	@echo "  compose-doctor - Show active Docker host and daemon architecture"
	@echo "  compose-up     - Auto-build OSRM data image, then start OSRM + API with safe sequencing"
	@echo "  compose-down   - Stop and remove running compose services"
	@echo "  compose-logs   - Tail API and OSRM logs"
	@echo "  compose-health - Quick runtime checks for API and OSRM services"
	@echo "  build-pkg      - Build the Python package for PyPI distribution"
	@echo "  publish        - Publish the Python package to PyPI (requires UV_PUBLISH_TOKEN in .env)"
	@echo "  clean          - Remove downloaded and processed data"
	@echo "  clean-pkg      - Remove Python build artifacts"

download-data:
	mkdir -p $(DATA_DIR)
	curl -L $(GEO_URL) -o $(DATA_DIR)/$(OSM_FILE)

process-osrm:
	@echo "Ensuring cross-platform emulation is available on Docker daemon..."
	-docker run --privileged --rm tonistiigi/binfmt --install all
	@echo "Building OSRM data builder for profile: $(PROFILE)..."
	docker build --pull -t osrm-data-builder --build-arg PROFILE=$(PROFILE) -f Dockerfile.builder .
	@echo "Local OSRM builder image ready."

compose-doctor:
	@echo "DOCKER_HOST=$${DOCKER_HOST:-<not set>}"
	@docker info --format 'Daemon: {{.OSType}}/{{.Architecture}}'

compose-up:
	$(MAKE) compose-doctor
	@echo "Ensuring cross-platform emulation is available on Docker daemon..."
	-docker run --privileged --rm tonistiigi/binfmt --install all
	$(COMPOSE) build osrm-data-builder
	$(COMPOSE) up -d --build osrm
	$(COMPOSE) up -d --build api
	$(MAKE) compose-health

compose-down:
	$(COMPOSE) down

compose-logs:
	$(COMPOSE) logs --tail=100 api osrm

compose-health:
	$(COMPOSE) ps
	@i=0; until $(COMPOSE) exec -T api curl -fsS http://localhost:8000/health >/dev/null; do \
		i=$$((i+1)); \
		if [ $$i -ge 30 ]; then echo "API health check failed after 30 attempts"; exit 1; fi; \
		sleep 1; \
	done
	@i=0; until $(COMPOSE) exec -T api curl -fsS "http://osrm-backend:5000/route/v1/driving/-84.0907,9.9281;-84.0833,9.9333?overview=false" >/dev/null; do \
		i=$$((i+1)); \
		if [ $$i -ge 30 ]; then echo "OSRM route check failed after 30 attempts"; exit 1; fi; \
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

