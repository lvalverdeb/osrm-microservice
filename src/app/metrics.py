from prometheus_fastapi_instrumentator import Instrumentator
from app.config import settings


def setup_metrics(app):
    Instrumentator().instrument(app).expose(
        app, endpoint=settings.METRICS_ENDPOINT, tags=["System"]
    )
