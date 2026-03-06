# Builder image to process OSRM data without host mounts
FROM ghcr.io/project-osrm/osrm-backend as builder

# Argument to select the profile (car, bicycle, foot)
ARG PROFILE=car

# Copy the source OSM file into the image
COPY ./data/costa-rica-latest.osm.pbf /data/costa-rica-latest.osm.pbf

# Run the processing steps and organize into profile subfolder
RUN mkdir -p /data/${PROFILE} && \
    osrm-extract -p /opt/${PROFILE}.lua /data/costa-rica-latest.osm.pbf && \
    mv /data/costa-rica-latest.osrm* /data/${PROFILE}/ && \
    osrm-partition /data/${PROFILE}/costa-rica-latest.osrm && \
    osrm-customize /data/${PROFILE}/costa-rica-latest.osrm

# Final stage just to hold the processed data
FROM alpine
COPY --from=builder /data /data
CMD ["tar", "cv -C /data ."]
