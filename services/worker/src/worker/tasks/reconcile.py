"""Reconciliation scheduler — runs at 15-30 minute intervals."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger
from app.persistence.models import IngestionCursor, SourceConnection, Tenant
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.reconcile.run_reconciliation")  # type: ignore[misc]
def run_reconciliation(source: str | None = None) -> dict[str, Any]:
    """Run reconciliation for all source connections."""
    import asyncio

    return asyncio.run(_run_reconciliation(source))


async def _run_reconciliation(source_filter: str | None = None) -> dict[str, Any]:
    logger.info("Running reconciliation")
    from worker.tasks.deletions import process_deletion
    from worker.tasks.fetch import fetch_source_object
    from worker.tasks.ingestion_helpers import connector_for, load_ingestion_context

    factory = get_session_factory()
    async with factory() as session:
        tenant_ids = [str(value) for value in (await session.scalars(select(Tenant.id))).all()]
    processed = 0
    failed = 0
    for tenant_id in tenant_ids:
        async with factory() as session:
            await set_tenant_context(session, tenant_id)
            connection_ids = [
                str(value)
                for value in (
                    await session.scalars(
                        select(SourceConnection.id).where(
                            SourceConnection.status == "connected",
                            *([SourceConnection.source == source_filter] if source_filter else []),
                        )
                    )
                ).all()
            ]
        for connection_id in connection_ids:
            try:
                context = await load_ingestion_context(tenant_id, connection_id)
                async with factory() as session:
                    await set_tenant_context(session, tenant_id)
                    cursor_row = await session.scalar(
                        select(IngestionCursor).where(
                            IngestionCursor.source_connection_id == connection_id,
                            IngestionCursor.cursor_type == "changes",
                        )
                    )
                    cursor = cursor_row.cursor_value if cursor_row else None
                page = await connector_for(context.connection.source).read_changes(
                    context.connection,
                    cursor,
                    context.credential,
                )
            except Exception:
                failed += 1
                logger.warning("Source reconciliation failed", connection_id=connection_id)
                continue
            for change in page.changes:
                external_id = change.get("external_object_id")
                if not isinstance(external_id, str) or not external_id:
                    continue
                message = {
                    "schemaVersion": 1,
                    "tenantId": tenant_id,
                    "connectionId": connection_id,
                    "source": context.connection.source.value,
                    "externalObjectId": external_id,
                    "sourceRevision": change.get("source_revision"),
                }
                if change.get("change_type") in {"deleted", "removed"}:
                    process_deletion.delay(message)  # type: ignore[union-attr]
                else:
                    fetch_source_object.delay(message)  # type: ignore[union-attr]
                processed += 1
            # Advance the provider cursor only after all resulting tasks have
            # been durably published. If publishing fails, the old cursor is
            # retained and reconciliation safely replays the page.
            async with factory() as session:
                await set_tenant_context(session, tenant_id)
                row = await session.scalar(
                    select(IngestionCursor).where(
                        IngestionCursor.source_connection_id == connection_id,
                        IngestionCursor.cursor_type == "changes",
                    )
                )
                if row is None:
                    row = IngestionCursor(
                        tenant_id=tenant_id,
                        source_connection_id=connection_id,
                        cursor_type="changes",
                    )
                    session.add(row)
                row.cursor_value = page.next_cursor
                connection = await session.scalar(
                    select(SourceConnection).where(SourceConnection.id == connection_id)
                )
                if connection is not None:
                    now = datetime.now(UTC)
                    connection.last_reconcile_at = now
                    connection.last_sync_at = now
                await session.commit()
    return {
        "status": "partial" if failed else "reconciled",
        "enqueued": processed,
        "failed_connections": failed,
    }
