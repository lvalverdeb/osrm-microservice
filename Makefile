# Use local data directory for processing
DATA_DIR = $(shell pwd)/data

OSM_FILE = costa-rica-latest.osm.pbf
OSRM_IMAGE = ghcr.io/project-osrm/osrm-backend
GEO_URL = http://download.geofabrik.de/central-america/$(OSM_FILE)

.PHONY: help download-data process-osrm clean

help:
	@echo "Available targets:"
	@echo "  download-data  - Download Costa Rica OSM data from Geofabrik"
	@echo "  process-osrm   - Extract, partition, and customize OSRM data (MLD)"
	@echo "  clean          - Remove downloaded and processed data"

download-data:
	mkdir -p $(DATA_DIR)
	curl -L $(GEO_URL) -o $(DATA_DIR)/$(OSM_FILE)

process-osrm:
	@echo "Building OSRM data using Docker context (No volumes)..."
	docker build -t osrm-data-builder -f Dockerfile.builder .
	@echo "Extracting processed data to local ./data folder..."
	docker run --rm osrm-data-builder tar -c -C /data . | tar -x -C ./data
	@echo "Local OSRM build complete."

clean:
	rm -rf $(DATA_DIR)
