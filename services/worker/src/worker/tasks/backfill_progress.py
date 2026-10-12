"""Persistent progress updates for user-triggered backfills."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.persistence.models import BackfillRun
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select


async def start_backfill_run(tenant_id: str, run_id: str | None) -> None:
    if not run_id:
        return
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await session.scalar(
            select(BackfillRun)
            .where(BackfillRun.id == UUID(run_id), BackfillRun.tenant_id == UUID(tenant_id))
            .with_for_update()
        )
        if run is not None and run.status == "queued":
            run.status = "scanning"
            run.started_at = datetime.now(UTC)
            await session.commit()


async def record_backfill_scan(
    tenant_id: str,
    run_id: str | None,
    *,
    discovered_count: int,
    enqueued_count: int,
) -> None:
    if not run_id:
        return
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await session.scalar(
            select(BackfillRun)
            .where(BackfillRun.id == UUID(run_id), BackfillRun.tenant_id == UUID(tenant_id))
            .with_for_update()
        )
        if run is None:
            return
        run.discovered_count = discovered_count
        run.enqueued_count = enqueued_count
        if enqueued_count == 0:
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
        else:
            run.status = "processing"
        await session.commit()


async def complete_backfill_item(
    tenant_id: str,
    run_id: str | None,
    *,
    failed: bool = False,
) -> None:
    """Record one terminal item outcome and close the run when it is complete."""
    if not run_id:
        return
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await session.scalar(
            select(BackfillRun)
            .where(BackfillRun.id == UUID(run_id), BackfillRun.tenant_id == UUID(tenant_id))
            .with_for_update()
        )
        if run is None or run.status in {"completed", "failed"}:
            return
        run.processed_count += 1
        if failed:
            run.failed_count += 1
        if run.processed_count >= run.enqueued_count:
            run.completed_at = datetime.now(UTC)
            run.status = "failed" if run.failed_count == run.enqueued_count else "completed"
        await session.commit()


async def fail_backfill_run(tenant_id: str, run_id: str | None, message: str) -> None:
    if not run_id:
        return
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await session.scalar(
            select(BackfillRun)
            .where(BackfillRun.id == UUID(run_id), BackfillRun.tenant_id == UUID(tenant_id))
            .with_for_update()
        )
        if run is not None:
            run.status = "failed"
            run.error_message = message[:500]
            run.completed_at = datetime.now(UTC)
            await session.commit()
