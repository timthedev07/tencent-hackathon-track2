"""User-scoped source connection listing and backfill publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import AuthenticatedUser
from app.domain.source_mode import ConnectionMode, active_connection_mode
from app.persistence.models import IdentityLink, SourceConnection
from app.retrieval.temporal import active_now

BackfillPublisher = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class SourceConnectionSummary:
    connection_id: UUID
    source: str
    display_name: str | None
    mode: ConnectionMode


class SourceConnectionNotFoundError(RuntimeError):
    """The requested connection is not linked to the current user."""


class SourceConnectionNotSyncableError(RuntimeError):
    """The requested connection cannot be synchronised."""


class SourceSyncUnavailableError(RuntimeError):
    """The backfill task could not be published."""


async def list_user_connections(
    session: AsyncSession,
    user: AuthenticatedUser,
) -> list[SourceConnectionSummary]:
    """List the current user's connections in the configured data mode."""
    statement = (
        select(SourceConnection)
        .join(
            IdentityLink,
            IdentityLink.source_connection_id == SourceConnection.id,
        )
        .where(
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.status == "connected",
            SourceConnection.mode == active_connection_mode(),
            IdentityLink.tenant_id == user.tenant_id,
            IdentityLink.user_id == user.user_id,
            IdentityLink.status == "verified",
            active_now(IdentityLink),
        )
        .distinct()
        .order_by(SourceConnection.source, SourceConnection.created_at)
    )
    rows = (await session.scalars(statement)).all()
    return [
        SourceConnectionSummary(
            connection_id=UUID(str(row.id)),
            source=str(row.source),
            display_name=row.display_name,
            mode=cast(ConnectionMode, row.mode),
        )
        for row in rows
    ]


def publish_source_backfill(connection_id: str, tenant_id: str) -> None:
    """Publish a backfill without putting provider credentials on the broker."""
    from worker.tasks.backfill import run_backfill

    run_backfill.apply_async(  # type: ignore[union-attr]
        args=[connection_id, tenant_id],
        queue="backfill",
        retry=False,
    )


async def queue_connection_sync(
    session: AsyncSession,
    user: AuthenticatedUser,
    connection_id: UUID,
    publisher: BackfillPublisher = publish_source_backfill,
) -> dict[str, str]:
    """Queue a backfill for one exact, live connection owned by the user."""
    statement = (
        select(SourceConnection)
        .join(
            IdentityLink,
            IdentityLink.source_connection_id == SourceConnection.id,
        )
        .where(
            SourceConnection.id == connection_id,
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.status == "connected",
            IdentityLink.tenant_id == user.tenant_id,
            IdentityLink.user_id == user.user_id,
            IdentityLink.status == "verified",
            active_now(IdentityLink),
        )
        .limit(1)
    )
    connection = await session.scalar(statement)
    if connection is None:
        raise SourceConnectionNotFoundError("SOURCE_CONNECTION_NOT_FOUND")
    if connection.mode != "delegated_oauth":
        raise SourceConnectionNotSyncableError("SOURCE_CONNECTION_NOT_SYNCABLE")

    try:
        await asyncio.to_thread(publisher, str(connection.id), str(user.tenant_id))
    except Exception as exc:
        raise SourceSyncUnavailableError("SOURCE_SYNC_FAILED") from exc

    return {"connectionId": str(connection.id), "status": "queued"}
