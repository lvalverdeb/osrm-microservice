# Docker image for the OSRM API Gateway.
#
# This is the gateway. Dockerfile.spike is a separate thing: a two-endpoint
# benchmark target, not a gateway, and never in front of traffic.

FROM rust:1-slim-bookworm AS builder

WORKDIR /build

# Cargo.lock is committed and --locked is used, so an image always builds the
# exact dependency versions that were tested rather than resolving afresh.
COPY gateway/Cargo.toml gateway/Cargo.lock ./
COPY gateway/src ./src

RUN cargo build --release --locked


FROM debian:bookworm-slim

ARG UID=1000
ARG HOST=0.0.0.0
ARG PORT=8000

ENV HOST=${HOST}
ENV PORT=${PORT}

# curl is here for the HEALTHCHECK only, exactly as in the Python image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u ${UID} appuser

COPY --from=builder /build/target/release/osrm-api-gateway /usr/local/bin/osrm-api-gateway

USER appuser

EXPOSE ${PORT}

# /ready, not /health: `curl -f` only fails on a non-2xx status, and /health
# answers 200 even when the engine is down, so the container would always
# report healthy.
HEALTHCHECK --interval=30s --timeout=8s --start-period=10s --retries=3 \
    CMD curl -f http://${HOST}:${PORT}/ready || exit 1

# No entrypoint script: WORKERS is tokio threads inside one process sharing one
# metrics registry, and the binary reads FORWARDED_ALLOW_IPS itself, so there is
# nothing to arrange before starting. exec form so the binary is PID 1 and
# receives SIGTERM from `docker stop` directly.
CMD ["osrm-api-gateway"]
