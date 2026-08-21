from unittest.mock import patch

import pytest
from fastapi import FastAPI


@pytest.fixture
def app():
    return FastAPI()


class TestTracingSetup:
    def test_setup_tracing_no_endpoint(self, app):
        with patch("app.tracing.settings") as mock_settings:
            mock_settings.OTLP_ENDPOINT = ""
            from app.tracing import setup_tracing
            setup_tracing(app)

    def test_setup_tracing_with_endpoint(self, app):
        with patch("app.tracing.settings") as mock_settings, \
             patch("app.tracing.OTLPSpanExporter") as mock_exporter, \
             patch("app.tracing.BatchSpanProcessor"), \
             patch("app.tracing.FastAPIInstrumentor.instrument_app") as mock_instrument, \
             patch("app.tracing.HTTPXClientInstrumentor") as mock_instrumentor_cls:
            mock_settings.OTLP_ENDPOINT = "http://localhost:4318/v1/traces"
            from app.tracing import setup_tracing
            setup_tracing(app)
            mock_exporter.assert_called_once_with(
                endpoint="http://localhost:4318/v1/traces"
            )
            mock_instrument.assert_called_once()
            mock_instrumentor_cls.return_value.instrument.assert_called_once()

    def test_setup_tracing_exporter_failure(self, app):
        with patch("app.tracing.settings") as mock_settings, \
             patch("app.tracing.OTLPSpanExporter", side_effect=RuntimeError("no collector")):
            mock_settings.OTLP_ENDPOINT = "http://localhost:4318/v1/traces"
            from app.tracing import setup_tracing
            setup_tracing(app)


@pytest.mark.asyncio
async def test_tracing_does_not_block_request():
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("healthy", "degraded")
