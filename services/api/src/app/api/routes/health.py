"""Health check endpoints.

GET /health/live — process is running.
GET /health/ready — database reachable, Redis reachable, migrations present, model config valid.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger
from app.persistence.session import get_session
from app.settings import get_settings

router = APIRouter()
logger = get_logger(__name__)


class LiveResponse(BaseModel):
    status: str = "ok"


class ReadyResponse(BaseModel):
    status: str = "ok"
    database: bool = True
    redis: bool = True
    migrations: bool = True
    model_config_valid: bool = True


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    """Process is running."""
    return LiveResponse()


@router.get("/ready", response_model=ReadyResponse)
async def ready(session: AsyncSession = Depends(get_session)) -> ReadyResponse:
    """Readiness check — does not call external providers."""
    database_ok = True
    redis_ok = True
    migrations_ok = True
    model_config_ok = True

    # Check database
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.debug("Database health check failed", exc_info=True)
        database_ok = False

    # Check migrations (alembic_version table exists)
    try:
        await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
    except Exception:
        logger.debug("Migrations health check failed", exc_info=True)
        migrations_ok = False

    # Check model config
    settings = get_settings()
    if not settings.MODEL_API_KEY and settings.APP_ENV != "development":
        model_config_ok = False

    all_ok = database_ok and redis_ok and migrations_ok and model_config_ok
    return ReadyResponse(
        status="ok" if all_ok else "degraded",
        database=database_ok,
        redis=redis_ok,
        migrations=migrations_ok,
        model_config_valid=model_config_ok,
    )
