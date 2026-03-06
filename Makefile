# Use local data directory for processing
DATA_DIR = $(shell pwd)/data

OSM_FILE = costa-rica-latest.osm.pbf
OSRM_IMAGE = ghcr.io/project-osrm/osrm-backend
GEO_URL = http://download.geofabrik.de/central-america/$(OSM_FILE)

# Profile selection (car, bicycle, foot) - defaults to car
PROFILE ?= car

.PHONY: help download-data process-osrm clean

help:
	@echo "Available targets:"
	@echo "  download-data  - Download Costa Rica OSM data from Geofabrik"
	@echo "  process-osrm   - Extract, partition, and customize OSRM data (PROFILE=$(PROFILE))"
	@echo "  clean          - Remove downloaded and processed data"

download-data:
	mkdir -p $(DATA_DIR)
	curl -L $(GEO_URL) -o $(DATA_DIR)/$(OSM_FILE)

process-osrm:
	@echo "Building OSRM data builder for profile: $(PROFILE)..."
	docker build -t osrm-data-builder --build-arg PROFILE=$(PROFILE) -f Dockerfile.builder .
	@echo "Local OSRM builder image ready."

clean:
	rm -rf $(DATA_DIR)
