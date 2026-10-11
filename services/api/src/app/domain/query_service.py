"""Query orchestration service — the core query pipeline.

This is the heart of the system. It orchestrates:
1. Authentication (done by dependency)
2. Candidate retrieval (vector + FTS)
3. Reciprocal-rank fusion
4. Local ACL projection
5. Live source reauthorisation
6. Authorised text load
7. Reranking
8. Context compilation
9. Model generation
10. Output verification
11. Audit commit
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.auth.oidc import AuthenticatedUser
from app.domain.query_execution import TERMINAL_QUERY_STATUSES, append_query_event
from app.observability.logging import get_logger
from app.persistence.models import QueryEvent, QueryRun
from app.persistence.session import get_session_factory, set_tenant_context

logger = get_logger(__name__)


def _failure_message(query_id: UUID, error_code: str | None) -> str:
    """Return a safe, actionable failure message without exposing source text."""
    messages = {
        "PIPELINE_ERROR": "The search pipeline failed. Check the server log for this query ID.",
        "PIPELINE_TASK_ERROR": (
            "The search task stopped unexpectedly. Check the server log for this query ID."
        ),
    }
    message = messages.get(error_code or "", "The search could not be completed.")
    return f"{message} Reference: {query_id}."


async def create_query_run(
    user_id: UUID,
    tenant_id: UUID,
    query_text: str,
    as_of: datetime | None = None,
) -> dict:
    """Create a new query run with encrypted query text (INV-005)."""
    cm = get_credential_manager()
    encrypted_query = cm.encrypt_bytes(query_text.encode("utf-8"))

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        run = QueryRun(
            tenant_id=tenant_id,
            user_id=user_id,
            query_ciphertext=encrypted_query.ciphertext,
            as_of=as_of,
            status="created",
        )
        session.add(run)
        await session.flush()
        run_id = UUID(str(run.id))
        await append_query_event(
            session,
            tenant_id,
            run_id,
            "query.queued",
            {"message": "Query queued for secure execution"},
        )
        await session.commit()
        await session.refresh(run)
        return {"id": run_id, "status": run.status}


def enqueue_query_run(query_id: UUID, tenant_id: UUID, user_id: UUID) -> None:
    """Publish a durable query-execution task to the dedicated worker queue."""
    from worker.tasks.queries import run_query

    run_query.apply_async(  # type: ignore[union-attr]
        kwargs={
            "query_id": str(query_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
        },
        queue="query_execution",
    )


async def ensure_query_access(query_id: UUID, user: AuthenticatedUser) -> None:
    """Return 404 unless the authenticated user owns the requested query."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        exists = await session.scalar(
            select(QueryRun.id).where(
                QueryRun.id == query_id,
                QueryRun.user_id == user.user_id,
                QueryRun.tenant_id == user.tenant_id,
            )
        )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "SOURCE_OBJECT_NOT_FOUND", "message": "Query not found."}},
        )


async def stream_events(
    query_id: UUID,
    user: AuthenticatedUser,
    after_sequence: int = 0,
) -> AsyncIterator[dict]:
    """Replay persisted progress events and then tail newly committed events.

    This projection is intentionally read-only. Query execution belongs to the
    worker and is guarded by a database lease, so reconnecting cannot run the
    pipeline a second time.
    """
    cursor = max(after_sequence, 0)
    factory = get_session_factory()

    while True:
        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            run = await session.scalar(
                select(QueryRun).where(
                    QueryRun.id == query_id,
                    QueryRun.user_id == user.user_id,
                    QueryRun.tenant_id == user.tenant_id,
                )
            )
            if run is None:
                return
            events = (
                await session.scalars(
                    select(QueryEvent)
                    .where(
                        QueryEvent.query_id == query_id,
                        QueryEvent.tenant_id == user.tenant_id,
                        QueryEvent.sequence_no > cursor,
                    )
                    .order_by(QueryEvent.sequence_no)
                )
            ).all()
            run_status = run.status

        for event in events:
            cursor = event.sequence_no
            data = {"queryId": str(query_id), **event.payload}
            yield {
                "id": str(event.sequence_no),
                "event": event.event_type,
                "data": json.dumps(data, separators=(",", ":")),
            }
            if event.event_type in {
                "query.completed",
                "query.partial",
                "query.denied",
                "query.failed",
            }:
                return

        if run_status in TERMINAL_QUERY_STATUSES:
            return
        await asyncio.sleep(0.25)


async def get_result(query_id: UUID, user: AuthenticatedUser) -> dict:
    """Fetch the final query result with citations."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        stmt = select(QueryRun).where(
            QueryRun.id == query_id,
            QueryRun.user_id == user.user_id,
            QueryRun.tenant_id == user.tenant_id,
        )
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {"code": "SOURCE_OBJECT_NOT_FOUND", "message": "Query not found."}
                },
            )

        cm = get_credential_manager()
        answer = None
        claims = []
        if run.answer_ciphertext:
            decrypted_answer = cm.decrypt_bytes(
                EncryptedPayload(ciphertext=run.answer_ciphertext)
            ).decode("utf-8")
            try:
                answer_payload = json.loads(decrypted_answer)
                answer = answer_payload.get("answer", decrypted_answer)
                claims = answer_payload.get("claims", [])
            except json.JSONDecodeError:
                answer = decrypted_answer

        # Load citations from evidence items
        from app.persistence.models import EvidenceItem

        evidence_stmt = (
            select(EvidenceItem)
            .where(EvidenceItem.query_id == query_id)
            .order_by(EvidenceItem.rank)
        )
        evidence_result = await session.execute(evidence_stmt)
        evidence_items = evidence_result.scalars().all()

        citations = [
            {
                "evidenceId": str(e.id),
                "source": e.source,
                "label": e.title_snapshot,
                "url": e.canonical_url,
            }
            for e in evidence_items
        ]

        partial_warnings = []
        if run.status == "partial" and run.partial_sources:
            for src in run.partial_sources:
                partial_warnings.append(
                    {
                        "source": src,
                        "reason": "live_authorisation_timeout",
                    }
                )

        return {
            "queryId": str(run.id),
            "status": run.status,
            "asOf": run.as_of.isoformat() if run.as_of else None,
            "answer": answer,
            "claims": claims,
            "citations": citations,
            "partialSourceWarnings": partial_warnings,
            "errorCode": run.error_code,
            "errorMessage": (
                _failure_message(query_id, run.error_code) if run.status == "failed" else None
            ),
        }
