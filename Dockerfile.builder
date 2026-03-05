# Builder image to process OSRM data without host mounts
FROM ghcr.io/project-osrm/osrm-backend as builder

# Copy the source OSM file into the image
COPY ./data/costa-rica-latest.osm.pbf /data/costa-rica-latest.osm.pbf

# Run the processing steps
RUN osrm-extract -p /opt/car.lua /data/costa-rica-latest.osm.pbf && \
    osrm-partition /data/costa-rica-latest.osrm && \
    osrm-customize /data/costa-rica-latest.osrm

# Final stage just to hold the processed data
FROM alpine
COPY --from=builder /data /data
CMD ["tar", "cv -C /data ."]
