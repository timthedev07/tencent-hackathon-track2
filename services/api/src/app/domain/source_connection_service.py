"""User-scoped source connection listing and synchronisation publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import AuthenticatedUser
from app.persistence.models import BackfillRun, IdentityLink, IngestionCursor, SourceConnection

BackfillPublisher = Callable[[str, str, str, str], None]


@dataclass(frozen=True, slots=True)
class SourceConnectionSummary:
    connection_id: UUID
    source: str
    display_name: str | None


class SourceConnectionNotFoundError(RuntimeError):
    """The requested connection is not linked to the current user."""


class SourceSyncUnavailableError(RuntimeError):
    """The backfill task could not be published."""


async def list_user_connections(
    session: AsyncSession,
    user: AuthenticatedUser,
) -> list[SourceConnectionSummary]:
    """List the current user's connected source identities."""
    statement = (
        select(SourceConnection)
        .join(IdentityLink, IdentityLink.source_connection_id == SourceConnection.id)
        .where(
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.status == "connected",
            IdentityLink.tenant_id == user.tenant_id,
            IdentityLink.user_id == user.user_id,
            IdentityLink.status == "verified",
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
        )
        for row in rows
    ]


def publish_source_backfill(
    connection_id: str,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> None:
    """Publish a backfill without putting provider credentials on the broker."""
    from worker.tasks.backfill import run_backfill

    run_backfill.apply_async(  # type: ignore[union-attr]
        args=[connection_id, tenant_id, user_id, run_id],
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
        .join(IdentityLink, IdentityLink.source_connection_id == SourceConnection.id)
        .where(
            SourceConnection.id == connection_id,
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.status == "connected",
            IdentityLink.tenant_id == user.tenant_id,
            IdentityLink.user_id == user.user_id,
            IdentityLink.status == "verified",
        )
        .limit(1)
    )
    connection = await session.scalar(statement)
    if connection is None:
        raise SourceConnectionNotFoundError("SOURCE_CONNECTION_NOT_FOUND")
    previous_run = await session.scalar(
        select(BackfillRun)
        .where(
            BackfillRun.tenant_id == user.tenant_id,
            BackfillRun.source_connection_id == connection.id,
            BackfillRun.requested_by_user_id == user.user_id,
        )
        .order_by(BackfillRun.created_at.desc())
        .limit(1)
    )
    if previous_run is not None and previous_run.failed_count:
        await session.execute(
            update(IngestionCursor)
            .where(
                IngestionCursor.tenant_id == user.tenant_id,
                IngestionCursor.source_connection_id == connection.id,
                IngestionCursor.cursor_type == "initial_scan",
            )
            .values(cursor_value=None)
        )
    run = BackfillRun(
        tenant_id=user.tenant_id,
        source_connection_id=connection.id,
        requested_by_user_id=user.user_id,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    try:
        await asyncio.to_thread(
            publisher,
            str(connection.id),
            str(user.tenant_id),
            str(user.user_id),
            str(run.id),
        )
    except Exception as exc:
        run.status = "failed"
        run.error_message = "The import worker is unavailable. Try again shortly."
        await session.commit()
        raise SourceSyncUnavailableError("SOURCE_SYNC_FAILED") from exc

    return {"connectionId": str(connection.id), "runId": str(run.id), "status": "queued"}


async def restart_connection_sync(
    session: AsyncSession,
    user: AuthenticatedUser,
    connection_id: UUID,
    publisher: BackfillPublisher = publish_source_backfill,
) -> dict[str, str]:
    """Stop the current visible run and queue a fresh backfill for the connection."""
    statement = (
        select(SourceConnection)
        .join(IdentityLink, IdentityLink.source_connection_id == SourceConnection.id)
        .where(
            SourceConnection.id == connection_id,
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.status == "connected",
            IdentityLink.tenant_id == user.tenant_id,
            IdentityLink.user_id == user.user_id,
            IdentityLink.status == "verified",
        )
        .limit(1)
    )
    connection = await session.scalar(statement)
    if connection is None:
        raise SourceConnectionNotFoundError("SOURCE_CONNECTION_NOT_FOUND")

    active_runs = await session.scalars(
        select(BackfillRun)
        .where(
            BackfillRun.tenant_id == user.tenant_id,
            BackfillRun.source_connection_id == connection.id,
            BackfillRun.requested_by_user_id == user.user_id,
            BackfillRun.status.in_(("queued", "scanning", "processing")),
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    for active_run in active_runs:
        active_run.status = "failed"
        active_run.error_message = "Import manually restarted."
        active_run.completed_at = now

    await session.execute(
        update(IngestionCursor)
        .where(
            IngestionCursor.tenant_id == user.tenant_id,
            IngestionCursor.source_connection_id == connection.id,
            IngestionCursor.cursor_type == "initial_scan",
        )
        .values(cursor_value=None)
    )

    run = BackfillRun(
        tenant_id=user.tenant_id,
        source_connection_id=connection.id,
        requested_by_user_id=user.user_id,
    )
    session.add(run)
    await session.commit()

    try:
        await asyncio.to_thread(
            publisher,
            str(connection.id),
            str(user.tenant_id),
            str(user.user_id),
            str(run.id),
        )
    except Exception as exc:
        run.status = "failed"
        run.error_message = "The import worker is unavailable. Try again shortly."
        await session.commit()
        raise SourceSyncUnavailableError("SOURCE_SYNC_FAILED") from exc

    return {"connectionId": str(connection.id), "runId": str(run.id), "status": "queued"}
