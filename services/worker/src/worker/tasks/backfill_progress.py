"""Persistent progress updates for user-triggered backfills."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.persistence.models import BackfillRun
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

_BACKFILL_LEASE_TIMEOUT = timedelta(minutes=3)


async def start_backfill_run(tenant_id: str, run_id: str | None) -> bool:
    if not run_id:
        return True
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await session.scalar(
            select(BackfillRun)
            .where(BackfillRun.id == UUID(run_id), BackfillRun.tenant_id == UUID(tenant_id))
            .with_for_update()
        )
        if run is None or run.status != "queued":
            if run is None or run.status not in {"scanning", "processing"}:
                return False
            last_activity = run.updated_at or run.started_at
            if (
                last_activity is None
                or datetime.now(UTC) - last_activity <= _BACKFILL_LEASE_TIMEOUT
            ):
                return False
            run.error_message = None
            run.completed_at = None
            run.discovered_count = 0
            run.enqueued_count = 0
            run.processed_count = 0
            run.failed_count = 0
        run.status = "scanning"
        run.started_at = datetime.now(UTC)
        await session.commit()
        return True


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
    failure_message: str | None = None,
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
            if failure_message and not run.error_message:
                run.error_message = failure_message[:500]
        if run.processed_count >= run.enqueued_count:
            run.completed_at = datetime.now(UTC)
            if run.failed_count == run.enqueued_count:
                run.status = "failed"
            elif run.failed_count:
                run.status = "completed_with_errors"
            else:
                run.status = "completed"
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
