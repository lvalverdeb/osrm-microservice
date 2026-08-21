import os

from fastapi import FastAPI
from prometheus_client import CollectorRegistry, multiprocess
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings


def _multiprocess_registry() -> CollectorRegistry | None:
    """Build a registry that aggregates metrics across uvicorn workers.

    prometheus_client keeps counters in process memory, so under `--workers N`
    each worker answers /metrics with only the requests it happened to serve and
    a scrape reports roughly 1/N of the traffic -- plausible-looking numbers that
    are silently wrong. Multiprocess mode has workers write to a shared
    directory that the scrape aggregates.

    PROMETHEUS_MULTIPROC_DIR must be exported before prometheus_client is
    imported, so it is set by the process supervisor (the rc.d script exports it
    whenever more than one worker is configured), not from application config.

    Returns:
        A registry backed by MultiProcessCollector, or None when the variable is
        unset, which keeps single-worker behaviour exactly as it was.
    """
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return None
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return registry


def setup_metrics(app: FastAPI) -> None:
    """Instrument the app and expose the Prometheus endpoint.

    Args:
        app: The FastAPI application to instrument.
    """
    registry = _multiprocess_registry()
    instrumentator = Instrumentator(registry=registry) if registry else Instrumentator()
    instrumentator.instrument(app).expose(
        app, endpoint=settings.METRICS_ENDPOINT, tags=["System"]
    )
