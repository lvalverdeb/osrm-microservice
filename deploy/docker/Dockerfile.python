FROM python:3.13-slim

ARG UID=1000
ARG HOST=0.0.0.0
ARG PORT=8000

ENV HOST=${HOST}
ENV PORT=${PORT}

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN useradd -m -u ${UID} appuser

WORKDIR /app

COPY pyproject.toml /app/

RUN uv pip install --system -r /app/pyproject.toml

COPY src/app /app/app
COPY deploy/docker/entrypoint.sh /entrypoint.sh

RUN chmod 555 /entrypoint.sh && chown -R appuser:appuser /app

USER appuser

EXPOSE ${PORT}

# /ready, not /health: `curl -f` only fails on a non-2xx status, and /health
# answers 200 even when the engine is down, so the container would always
# report healthy.
HEALTHCHECK --interval=30s --timeout=8s --start-period=10s --retries=3 \
    CMD curl -f http://${HOST}:${PORT}/ready || exit 1

# The entrypoint reads HOST/PORT/WORKERS/FORWARDED_ALLOW_IPS from the environment.
# The exec-form CMD it replaces could not interpolate the ENVs above, so HOST and
# PORT were set but only ever honoured by the HEALTHCHECK.
ENTRYPOINT ["/entrypoint.sh"]
