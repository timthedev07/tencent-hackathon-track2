"""FastAPI application bootstrap."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.routes.audit import router as audit_router
from app.api.routes.dev_auth import router as dev_auth_router
from app.api.routes.evidence import router as evidence_router
from app.api.routes.health import router as health_router
from app.api.routes.oauth import router as oauth_router
from app.api.routes.queries import router as queries_router
from app.api.routes.webhooks import router as webhooks_router
from app.observability.logging import (
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
    request_id_from_header,
)
from app.observability.metrics import MetricsMiddleware, metrics_app
from app.observability.tracing import configure_tracing, instrument_app
from app.settings import get_settings

logger = get_logger(__name__)
HTTP_CLIENT_ERROR = 400
HTTP_SERVER_ERROR = 500


@asynccontextmanager  # type: ignore[misc]
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # type: ignore[arg-type]
    """Application startup and shutdown."""
    configure_logging()
    get_settings().validate_for_startup()
    try:
        if configure_tracing() is not None:
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

    settings = get_settings()

    @app.middleware("http")
    async def request_logging(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Emit one correlated, safe lifecycle event for every HTTP request."""
        clear_context()
        request_id = request_id_from_header(request.headers.get("X-Request-ID"))
        bind_context(
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )
        started = perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "HTTP request failed",
                status_code=HTTP_SERVER_ERROR,
                latency_ms=round((perf_counter() - started) * 1000, 2),
            )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            if response is not None:
                latency_ms = round((perf_counter() - started) * 1000, 2)
                route_template = getattr(request.scope.get("route"), "path", None)
                if route_template:
                    bind_context(http_route=route_template)
                log_method = (
                    logger.error
                    if response.status_code >= HTTP_SERVER_ERROR
                    else logger.warning
                    if response.status_code >= HTTP_CLIENT_ERROR
                    else logger.info
                )
                log_method(
                    "HTTP request completed",
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                )
            clear_context()

    # ── CORS ──────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        expose_headers=["X-Request-ID"],
    )

    # ── Metrics ────────────────────────────────────────────────────
    app.add_middleware(MetricsMiddleware)

    @app.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Apply browser hardening headers to every API response."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

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
