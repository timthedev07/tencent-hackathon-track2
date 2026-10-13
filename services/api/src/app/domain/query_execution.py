"""Durable query execution leases and replayable progress events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.types import QueryStatus
from app.persistence.models import QueryEvent, QueryRun
from app.persistence.session import get_session_factory, set_tenant_context

TERMINAL_QUERY_STATUSES = frozenset(
    {
        QueryStatus.COMPLETED.value,
        QueryStatus.PARTIAL.value,
        QueryStatus.DENIED.value,
        QueryStatus.FAILED.value,
    }
)
QUERY_LEASE_SECONDS = 360


class LeaseState(StrEnum):
    ACQUIRED = "acquired"
    BUSY = "busy"
    TERMINAL = "terminal"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class LeaseResult:
    state: LeaseState
    token: str | None = None
    attempt: int | None = None


async def _locked_query_run(
    session: AsyncSession,
    tenant_id: UUID,
    query_id: UUID,
) -> QueryRun | None:
    return await session.scalar(
        select(QueryRun)
        .where(QueryRun.id == query_id, QueryRun.tenant_id == tenant_id)
        .with_for_update()
    )


async def append_query_event(
    session: AsyncSession,
    tenant_id: UUID,
    query_id: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    status: str | None = None,
    force: bool = False,
) -> QueryEvent | None:
    """Append an event while serialising sequence numbers on the query row."""
    run = await _locked_query_run(session, tenant_id, query_id)
    if run is None:
        raise ValueError(f"Query run {query_id} not found")
    if status is not None and not force and run.status == status:
        return None
    if status is not None:
        run.status = status

    last_sequence = await session.scalar(
        select(func.max(QueryEvent.sequence_no)).where(QueryEvent.query_id == query_id)
    )
    event = QueryEvent(
        tenant_id=tenant_id,
        query_id=query_id,
        sequence_no=(last_sequence or 0) + 1,
        event_type=event_type,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    return event


async def record_query_stage(
    query_id: UUID,
    tenant_id: UUID,
    *,
    status: str,
    event_type: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist a pipeline transition before the corresponding work begins."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        await append_query_event(
            session,
            tenant_id,
            query_id,
            event_type,
            {"message": message, **(payload or {})},
            status=status,
        )
        await session.commit()


async def acquire_query_lease(
    query_id: UUID,
    tenant_id: UUID,
    *,
    lease_seconds: int = QUERY_LEASE_SECONDS,
) -> LeaseResult:
    """Acquire a lease so duplicate worker deliveries cannot execute together."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await _locked_query_run(session, tenant_id, query_id)
        if run is None:
            return LeaseResult(LeaseState.MISSING)
        if run.status in TERMINAL_QUERY_STATUSES:
            return LeaseResult(LeaseState.TERMINAL, attempt=run.execution_attempt)

        now = datetime.now(UTC)
        if (
            run.execution_lease_token
            and run.execution_lease_expires_at
            and run.execution_lease_expires_at > now
        ):
            return LeaseResult(LeaseState.BUSY, attempt=run.execution_attempt)

        token = str(uuid4())
        run.execution_attempt += 1
        run.execution_lease_token = token
        run.execution_lease_expires_at = now + timedelta(seconds=lease_seconds)
        await session.commit()
        return LeaseResult(LeaseState.ACQUIRED, token=token, attempt=run.execution_attempt)


async def release_query_lease(query_id: UUID, tenant_id: UUID, token: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await _locked_query_run(session, tenant_id, query_id)
        if run is not None and run.execution_lease_token == token:
            run.execution_lease_token = None
            run.execution_lease_expires_at = None
            await session.commit()


async def fail_query_execution(
    query_id: UUID,
    tenant_id: UUID,
    *,
    error_code: str,
    message: str,
) -> None:
    """Persist a terminal failure without exposing exception details."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = await _locked_query_run(session, tenant_id, query_id)
        if run is None or run.status in TERMINAL_QUERY_STATUSES:
            return
        run.error_code = error_code
        run.completed_at = datetime.now(UTC)
        run.execution_lease_token = None
        run.execution_lease_expires_at = None
        await append_query_event(
            session,
            tenant_id,
            query_id,
            "query.failed",
            {"message": message, "errorCode": error_code},
            status=QueryStatus.FAILED.value,
            force=True,
        )
        await session.commit()
