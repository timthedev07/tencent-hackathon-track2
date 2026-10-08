"""PostgreSQL/Redis integration proof for durable query execution."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from app.auth.oidc import AuthenticatedUser
from app.domain.query_execution import (
    LeaseState,
    acquire_query_lease,
    release_query_lease,
)
from app.domain.query_service import create_query_run, stream_events
from app.persistence.models import (
    AuditEvent,
    EvidenceCapsule,
    EvidenceItem,
    QueryEvent,
    QueryRun,
    Tenant,
    User,
)
from app.persistence.session import dispose_engine, get_session_factory, set_tenant_context
from sqlalchemy import func, select

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
        reason="set RUN_POSTGRES_INTEGRATION=1 for PostgreSQL integration tests",
    ),
]

EXPECTED_COMPLETED_EVENTS = [
    "query.queued",
    "query.authenticated",
    "query.retrieving",
    "query.reauthorising",
    "query.reranking",
    "query.generating",
    "query.verifying",
    "query.auditing",
    "query.completed",
]


@pytest.fixture(autouse=True)
async def close_async_pool_per_test() -> AsyncIterator[None]:
    """Dispose the async pool before pytest closes each test's event loop."""
    yield
    await dispose_engine()


@pytest.fixture
async def alice() -> AuthenticatedUser:
    factory = get_session_factory()
    async with factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.external_org_key == "dev-org"))
        assert tenant is not None, "run scripts/seed_fixtures.py before this suite"
        await set_tenant_context(session, tenant.id)
        user = await session.scalar(
            select(User).where(
                User.tenant_id == tenant.id,
                User.oidc_subject == "alice",
            )
        )
        assert user is not None, "Chronicle Alice fixture is not seeded"
        return AuthenticatedUser(
            user_id=UUID(str(user.id)),
            tenant_id=UUID(str(tenant.id)),
            oidc_subject=user.oidc_subject,
            display_name=user.display_name,
            email=user.email,
            is_compliance_user=user.is_compliance_user,
        )


async def _new_query(actor: AuthenticatedUser, suffix: str) -> UUID:
    created = await create_query_run(
        actor.user_id,
        actor.tenant_id,
        f"What caused the payment gateway outage? Integration case: {suffix}",
    )
    return UUID(str(created["id"]))


async def _stored_events(query_id: UUID, tenant_id: UUID) -> list[tuple[int, str]]:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        rows = (
            await session.execute(
                select(QueryEvent.sequence_no, QueryEvent.event_type)
                .where(QueryEvent.query_id == query_id)
                .order_by(QueryEvent.sequence_no)
            )
        ).all()
        return [(row.sequence_no, row.event_type) for row in rows]


async def test_only_one_concurrent_lease_is_acquired(alice: AuthenticatedUser) -> None:
    query_id = await _new_query(alice, "active lease contention")

    first, second = await asyncio.gather(
        acquire_query_lease(query_id, alice.tenant_id),
        acquire_query_lease(query_id, alice.tenant_id),
    )

    assert {first.state, second.state} == {LeaseState.ACQUIRED, LeaseState.BUSY}
    acquired = first if first.state == LeaseState.ACQUIRED else second
    assert acquired.token is not None
    assert acquired.attempt == 1
    await release_query_lease(query_id, alice.tenant_id, acquired.token)


async def test_expired_lease_is_recovered_without_old_owner_release(
    alice: AuthenticatedUser,
) -> None:
    query_id = await _new_query(alice, "expired lease recovery")
    expired = await acquire_query_lease(query_id, alice.tenant_id, lease_seconds=-1)
    recovered = await acquire_query_lease(query_id, alice.tenant_id)

    assert expired.state == LeaseState.ACQUIRED
    assert recovered.state == LeaseState.ACQUIRED
    assert recovered.attempt == 2
    assert expired.token is not None
    assert recovered.token is not None
    assert recovered.token != expired.token

    await release_query_lease(query_id, alice.tenant_id, expired.token)
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, alice.tenant_id)
        run = await session.scalar(select(QueryRun).where(QueryRun.id == query_id))
        assert run is not None
        assert run.execution_lease_token == recovered.token

    await release_query_lease(query_id, alice.tenant_id, recovered.token)


@pytest.mark.celery
@pytest.mark.skipif(
    os.getenv("RUN_CELERY_INTEGRATION") != "1",
    reason="set RUN_CELERY_INTEGRATION=1 with a live worker and Redis",
)
async def test_celery_redelivery_is_terminal_and_does_not_duplicate_events(
    alice: AuthenticatedUser,
) -> None:
    from worker.tasks.queries import run_query

    query_task: Any = run_query
    query_id = await _new_query(alice, "Celery redelivery")
    task_kwargs = {
        "query_id": str(query_id),
        "tenant_id": str(alice.tenant_id),
        "user_id": str(alice.user_id),
    }

    first_task = query_task.apply_async(kwargs=task_kwargs, queue="query_execution")
    first_result = await asyncio.to_thread(first_task.get, timeout=90)
    second_task = query_task.apply_async(kwargs=task_kwargs, queue="query_execution")
    second_result = await asyncio.to_thread(second_task.get, timeout=30)

    assert first_result["status"] == "completed"
    assert second_result["status"] == LeaseState.TERMINAL.value

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, alice.tenant_id)
        run = await session.scalar(select(QueryRun).where(QueryRun.id == query_id))
        assert run is not None
        assert run.execution_attempt == 1
        assert run.execution_lease_token is None
        assert run.execution_lease_expires_at is None

    events = await _stored_events(query_id, alice.tenant_id)
    assert [sequence for sequence, _ in events] == list(range(1, 10))
    assert [event_type for _, event_type in events] == EXPECTED_COMPLETED_EVENTS


async def test_audit_failure_exposes_no_answer_and_replays_only_failure(
    alice: AuthenticatedUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.retrieval import pipeline

    query_id = await _new_query(alice, "forced audit failure")

    async def reject_audit_commit(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(pipeline, "_audit_commit", reject_audit_commit)
    result = await pipeline.run_query_pipeline(query_id, alice)
    assert result["status"] == "failed"

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, alice.tenant_id)
        run = await session.scalar(select(QueryRun).where(QueryRun.id == query_id))
        assert run is not None
        assert run.status == "failed"
        assert run.answer_ciphertext is None
        assert run.error_code == "PIPELINE_ERROR"
        assert (
            await session.scalar(
                select(func.count(EvidenceItem.id)).where(EvidenceItem.query_id == query_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count(EvidenceCapsule.id)).where(EvidenceCapsule.query_id == query_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.query_id == query_id)
            )
            == 0
        )

    events = await _stored_events(query_id, alice.tenant_id)
    event_types = [event_type for _, event_type in events]
    assert event_types[-1] == "query.failed"
    assert "query.completed" not in event_types

    replayed = [event async for event in stream_events(query_id, alice)]
    assert [int(event["id"]) for event in replayed] == list(range(1, len(events) + 1))
    terminal = replayed[-1]
    assert terminal["event"] == "query.failed"
    assert json.loads(terminal["data"])["errorCode"] == "PIPELINE_ERROR"

    resumed = [
        event
        async for event in stream_events(
            query_id,
            alice,
            after_sequence=len(events) - 1,
        )
    ]
    assert [event["event"] for event in resumed] == ["query.failed"]
