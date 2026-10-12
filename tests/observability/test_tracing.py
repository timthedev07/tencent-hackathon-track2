from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.observability import tracing


def test_configure_tracing_with_endpoint_builds_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        APP_ENV="test",
        OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:4318/v1/traces",
    )
    monkeypatch.setattr(tracing, "get_settings", lambda: settings)

    provider = tracing.configure_tracing()
    assert provider is not None

    try:
        assert provider.resource.attributes["deployment.environment"] == "test"
    finally:
        provider.shutdown()
        tracing._tracer_provider = None
