"""Durable background execution for user queries."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.auth.oidc import AuthenticatedUser
from app.domain.query_execution import (
    LeaseState,
    acquire_query_lease,
    fail_query_execution,
    release_query_lease,
)
from app.persistence.models import QueryRun, User
from app.persistence.session import dispose_engine, get_session_factory, set_tenant_context
from app.retrieval.pipeline import run_query_pipeline
from sqlalchemy import select

from worker.celery_app import celery_app


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="worker.tasks.queries.run_query",
    max_retries=20,
)
def run_query(
    self: Any,
    query_id: str,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Execute one query once; retry deliveries that encounter an active lease."""
    try:
        result = asyncio.run(_run_query_once(UUID(query_id), UUID(tenant_id), UUID(user_id)))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=15) from exc

    if result["status"] == LeaseState.BUSY.value:
        raise self.retry(countdown=30)
    return result


async def _run_query_once(query_id: UUID, tenant_id: UUID, user_id: UUID) -> dict[str, Any]:
    """Keep the process-global async pool bound to only this invocation's loop."""
    try:
        return await _run_query(query_id, tenant_id, user_id)
    finally:
        await dispose_engine()


async def _run_query(query_id: UUID, tenant_id: UUID, user_id: UUID) -> dict[str, Any]:
    lease = await acquire_query_lease(query_id, tenant_id)
    if lease.state != LeaseState.ACQUIRED or lease.token is None:
        return {
            "status": lease.state.value,
            "query_id": str(query_id),
            "attempt": lease.attempt,
        }

    try:
        user = await _load_query_user(query_id, tenant_id, user_id)
        if user is None:
            await fail_query_execution(
                query_id,
                tenant_id,
                error_code="QUERY_ACTOR_MISSING",
                message="The query requester could not be resolved.",
            )
            return {"status": "failed", "query_id": str(query_id)}
        return await run_query_pipeline(query_id, user)
    finally:
        await release_query_lease(query_id, tenant_id, lease.token)


async def _load_query_user(
    query_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
) -> AuthenticatedUser | None:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        user = await session.scalar(
            select(User)
            .join(QueryRun, QueryRun.user_id == User.id)
            .where(
                QueryRun.id == query_id,
                QueryRun.tenant_id == tenant_id,
                QueryRun.user_id == user_id,
                User.id == user_id,
                User.tenant_id == tenant_id,
            )
        )
        if user is None:
            return None
        return AuthenticatedUser(
            user_id=UUID(str(user.id)),
            tenant_id=UUID(str(user.tenant_id)),
            oidc_subject=user.oidc_subject,
            display_name=user.display_name,
            email=user.email,
            is_compliance_user=user.is_compliance_user,
        )
