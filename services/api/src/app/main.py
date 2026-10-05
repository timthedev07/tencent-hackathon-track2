"""FastAPI application bootstrap."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.audit import router as audit_router
from app.api.routes.dev_auth import router as dev_auth_router
from app.api.routes.evidence import router as evidence_router
from app.api.routes.health import router as health_router
from app.api.routes.oauth import router as oauth_router
from app.api.routes.queries import router as queries_router
from app.api.routes.webhooks import router as webhooks_router
from app.observability.logging import configure_logging, get_logger
from app.observability.metrics import MetricsMiddleware, metrics_app
from app.observability.tracing import configure_tracing, instrument_app

logger = get_logger(__name__)


@asynccontextmanager  # type: ignore[misc]
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # type: ignore[arg-type]
    """Application startup and shutdown."""
    configure_logging()
    try:
        configure_tracing()
        instrument_app(app)
    except Exception:
        logger.debug("Tracing setup skipped (optional in development)", exc_info=True)
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Enterprise AI Assistant",
        description="Permission-safe cross-platform enterprise AI assistant",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # ── Metrics ────────────────────────────────────────────────────
    app.add_middleware(MetricsMiddleware)

    # ── Routes ─────────────────────────────────────────────────────
    app.include_router(health_router, prefix="/health", tags=["health"])
    app.include_router(queries_router, prefix="/v1", tags=["queries"])
    app.include_router(dev_auth_router, prefix="/v1/auth", tags=["auth"])
    app.include_router(evidence_router, prefix="/v1", tags=["evidence"])
    app.include_router(audit_router, prefix="/v1", tags=["audit"])
    app.include_router(webhooks_router, prefix="/v1/webhooks", tags=["webhooks"])
    app.include_router(oauth_router, prefix="/v1/oauth", tags=["oauth"])
    app.mount("/metrics", metrics_app)

    return app


app = create_app()
