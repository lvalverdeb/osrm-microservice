import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings

logger = logging.getLogger(__name__)


def setup_tracing(app: FastAPI) -> None:
    provider = TracerProvider()
    if settings.OTLP_ENDPOINT:
        try:
            exporter = OTLPSpanExporter(endpoint=settings.OTLP_ENDPOINT)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("Tracing enabled, exporting to %s", settings.OTLP_ENDPOINT)
        except Exception as e:
            logger.warning("Failed to configure OTLP exporter, tracing disabled: %s", e)
    else:
        logger.info("OTLP_ENDPOINT not set, tracing disabled")

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
