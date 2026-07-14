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

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://${HOST}:${PORT}/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
