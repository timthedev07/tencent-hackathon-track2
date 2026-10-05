"""Prometheus metrics — 17 named metrics with exact label cardinalities."""

from __future__ import annotations

from typing import Any

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    make_asgi_app,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ── Metrics (per spec Section 35) ──────────────────────────────────

CONNECTOR_LAG = Gauge(
    "connector_lag_seconds",
    "Time since last successful connector sync",
    labelnames=["source"],
)

INGESTION_ACTIVE_VERSIONS = Gauge(
    "ingestion_active_versions_total",
    "Total active object versions per source",
    labelnames=["source"],
)

INGESTION_FAILURES = Counter(
    "ingestion_failures_total",
    "Total ingestion failures",
    labelnames=["source", "error_code"],
)

DEAD_LETER_TASKS = Counter(
    "dead_letter_tasks_total",
    "Total dead-letter tasks",
    labelnames=["queue"],
)

CELERY_OLDEST_TASK_AGE = Gauge(
    "celery_oldest_task_age_seconds",
    "Age of the oldest pending Celery task",
    labelnames=["queue"],
)

PROVIDER_ACCESS_CHECK_LATENCY = Histogram(
    "provider_access_check_latency_seconds",
    "Latency of provider access checks",
    labelnames=["source"],
)

PROVIDER_ACCESS_CHECK_FAILURES = Counter(
    "provider_access_check_failures_total",
    "Total provider access check failures",
    labelnames=["source", "reason"],
)

QUERY_LATENCY = Histogram(
    "query_latency_seconds",
    "End-to-end query latency",
    labelnames=["status"],
)

QUERY_PARTIAL = Counter(
    "query_partial_total",
    "Total partial query results",
    labelnames=["source", "reason"],
)

RETRIEVAL_CANDIDATES = Gauge(
    "retrieval_candidates_total",
    "Candidate counts at each retrieval stage",
    labelnames=["stage", "source"],
)

RETRIEVAL_UNDERFILL = Counter(
    "retrieval_underfill_total",
    "Total retrieval underfill events",
    labelnames=["source"],
)

CITATION_VERIFICATION_FAILURES = Counter(
    "citation_verification_failures_total",
    "Total citation verification failures",
    labelnames=["reason"],
)

AUDIT_CHAIN_VERIFICATION_FAILURES = Counter(
    "audit_chain_verification_failures_total",
    "Total audit chain verification failures",
)

MODEL_REQUESTS = Counter(
    "model_requests_total",
    "Total model API requests",
    labelnames=["model"],
)

MODEL_LATENCY = Histogram(
    "model_latency_seconds",
    "Model API latency",
    labelnames=["model"],
)

MODEL_COST = Counter(
    "model_estimated_cost_total",
    "Estimated model cost",
    labelnames=["model"],
)

EVENT_LOOP_LAG = Gauge(
    "event_loop_lag_seconds",
    "Event loop lag",
)

# ── HTTP middleware for request timing ──────────────────────────────

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "HTTP request latency",
    labelnames=["method", "path", "status"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that records HTTP request metrics."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        import time

        method = request.method
        path = request.url.path
        start = time.monotonic()
        response = await call_next(request)
        latency = time.monotonic() - start
        status = response.status_code
        REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
        REQUEST_LATENCY.labels(method=method, path=path, status=str(status)).observe(latency)
        return response


metrics_middleware = MetricsMiddleware


# Prometheus ASGI app, mounted at /metrics in main.py
metrics_app = make_asgi_app()
