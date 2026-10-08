"""Database session management and RLS helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the global async engine (singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.SQL_ECHO,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the global session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a database session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def set_tenant_context(session: AsyncSession, tenant_id: UUID | str) -> None:
    """Set the RLS tenant context for the current transaction.

    This must be called at the beginning of every tenant-scoped transaction.
    The tenant_id is server-derived (never from client input), so safe to inline.
    PostgreSQL's SET LOCAL does not support bind parameters.
    """
    tid = str(tenant_id).replace("'", "''")
    await session.execute(text(f"SET LOCAL app.tenant_id = '{tid}'"))


async def dispose_engine() -> None:
    """Dispose the global engine — for testing and shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
