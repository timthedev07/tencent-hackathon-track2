"""Backfill task — re-ingest historical data."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.persistence.models import IngestionCursor
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.backfill.run_backfill")  # type: ignore[misc]
def run_backfill(
    source_connection_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Backfill all objects for a source connection."""
    from worker.async_runner import run_async

    return run_async(_run_backfill(source_connection_id, tenant_id, user_id, run_id))


async def _run_backfill(
    source_connection_id: str,
    tenant_id: str | None,
    user_id: str | None,
    run_id: str | None,
) -> dict[str, Any]:
    if not tenant_id:
        raise ValueError("TENANT_CONTEXT_REQUIRED")
    from worker.tasks.backfill_progress import (
        fail_backfill_run,
        record_backfill_scan,
        start_backfill_run,
    )
    from worker.tasks.fetch import fetch_source_object
    from worker.tasks.ingestion_helpers import connector_for, load_ingestion_context

    logger.info("Running backfill", source_connection_id=source_connection_id)
    try:
        await start_backfill_run(tenant_id, run_id)
        context = await load_ingestion_context(tenant_id, source_connection_id, user_id)
    except Exception as exc:
        await fail_backfill_run(tenant_id, run_id, str(exc))
        raise
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        row = await session.scalar(
            select(IngestionCursor).where(
                IngestionCursor.source_connection_id == source_connection_id,
                IngestionCursor.cursor_type == "initial_scan",
            )
        )
        cursor = row.cursor_value if row else None
    try:
        page = await connector_for(context.connection.source).initial_scan(
            context.connection,
            cursor,
            context.credential,
        )
    except Exception as exc:
        await fail_backfill_run(tenant_id, run_id, str(exc))
        raise
    enqueued = 0
    for item in page.objects:
        external_id = item.get("external_object_id")
        if isinstance(external_id, str) and external_id:
            message: dict[str, Any] = {
                "schemaVersion": 1,
                "tenantId": tenant_id,
                "connectionId": source_connection_id,
                "source": context.connection.source.value,
                "externalObjectId": external_id,
                "sourceRevision": item.get("source_revision"),
            }
            if user_id:
                message["userId"] = user_id
            if run_id:
                message["backfillRunId"] = run_id
            fetch_source_object.delay(message)  # type: ignore[union-attr]
            enqueued += 1
    await record_backfill_scan(
        tenant_id,
        run_id,
        discovered_count=len(page.objects),
        enqueued_count=enqueued,
    )
    # The scan cursor is advanced after the page's fetch tasks are published;
    # a publish failure leaves the old cursor in place for a safe replay.
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        row = await session.scalar(
            select(IngestionCursor).where(
                IngestionCursor.source_connection_id == source_connection_id,
                IngestionCursor.cursor_type == "initial_scan",
            )
        )
        if row is None:
            row = IngestionCursor(
                tenant_id=tenant_id,
                source_connection_id=source_connection_id,
                cursor_type="initial_scan",
            )
            session.add(row)
        row.cursor_value = page.next_cursor
        await session.commit()
    return {
        "status": "backfilled",
        "source_connection_id": source_connection_id,
        "enqueued": enqueued,
    }
