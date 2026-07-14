# Builder image to process OSRM data without host mounts
FROM --platform=linux/amd64 ghcr.io/project-osrm/osrm-backend:latest AS builder

ARG PROFILE=car
ARG OSM_FILE=costa-rica-latest.osm.pbf

COPY ./data/${OSM_FILE} /data/${OSM_FILE}

RUN mkdir -p /data/${PROFILE} && \
    osrm-extract -p /opt/${PROFILE}.lua /data/${OSM_FILE} && \
    OSM_BASE="${OSM_FILE%.osm.pbf}" && \
    mv /data/${OSM_BASE}.osrm* /data/${PROFILE}/ && \
    osrm-partition /data/${PROFILE}/${OSM_BASE}.osrm && \
    osrm-customize /data/${PROFILE}/${OSM_BASE}.osrm

FROM alpine
COPY --from=builder /data /data
CMD ["tar", "cvf", "-", "-C", "/data", "."]
