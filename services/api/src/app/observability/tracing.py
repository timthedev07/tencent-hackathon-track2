"""OpenTelemetry tracing configuration.

Recommended spans (21):
http.request, auth.oidc_validate, query.candidate_retrieval,
query.vector_search, query.lexical_search, query.rrf,
query.local_acl, query.live_authorisation, provider.check_access,
query.load_authorised_text, query.rerank, query.context_compile,
model.generate, query.verify, audit.commit, evidence.reauthorise,
ingestion.fetch, ingestion.normalise, ingestion.acl,
ingestion.chunk, ingestion.embed

Constraint: Do NOT place source text in span attributes.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.settings import get_settings

_tracer_provider: TracerProvider | None = None


def configure_tracing() -> TracerProvider:
    """Configure OpenTelemetry tracing."""
    global _tracer_provider
    if _tracer_provider is not None:
        return _tracer_provider

    settings = get_settings()
    resource = Resource.create(
        {
            "service.name": "enterprise-ai-assistant",
            "service.version": "0.1.0",
            "deployment.environment": settings.APP_ENV,
        }
    )

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=f"{settings.APP_BASE_URL}/v1/traces",
        timeout=5,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer_provider = provider

    return provider


def instrument_app(app: Any) -> None:
    """Instrument FastAPI, HTTPX, SQLAlchemy, Redis."""
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine="default")
    RedisInstrumentor().instrument()


def get_tracer(module_name: str) -> trace.Tracer:
    """Return a tracer for the given module."""
    return trace.get_tracer(module_name)
