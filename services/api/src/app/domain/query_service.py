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
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.auth.oidc import AuthenticatedUser
from app.observability.logging import get_logger
from app.persistence.models import QueryRun
from app.persistence.session import get_session_factory, set_tenant_context

logger = get_logger(__name__)

_pipeline_tasks: dict[UUID, asyncio.Task[dict]] = {}
_pipeline_tasks_lock = asyncio.Lock()

_TERMINAL_STATUSES = {"completed", "partial", "denied", "failed"}
_PROGRESS_BY_STATUS = {
    # Each event represents work currently underway, rather than a synthetic
    # reconstruction of all stages after the query has already finished.
    "created": ("query.authenticated", "Checking your identity"),
    "authenticated": ("query.retrieving", "Searching connected sources"),
    "candidates": ("query.reauthorising", "Checking your current access"),
    "reauthorised": ("query.reranking", "Ranking authorised evidence"),
    "reranked": ("query.generating", "Preparing a grounded answer"),
    "compiled": ("query.generating", "Generating an answer"),
    "generated": ("query.verifying", "Verifying citations"),
    "verified": ("query.auditing", "Saving the audit record"),
}
_EMPTY_WORKSPACE_CODE = "NO_SEARCHABLE_CONTENT"
_EMPTY_WORKSPACE_MESSAGE = (
    "No searchable content is available in your connected workspaces yet. "
    "Add content and wait for it to be indexed, then try again."
)


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


async def _query_state(
    query_id: UUID,
    user: AuthenticatedUser,
) -> tuple[str | None, str | None]:
    """Read the query state for the current tenant and user."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        result = await session.execute(
            select(QueryRun.status, QueryRun.error_code).where(
                QueryRun.id == query_id,
                QueryRun.user_id == user.user_id,
                QueryRun.tenant_id == user.tenant_id,
            )
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row else (None, None)


async def _run_pipeline_once(query_id: UUID, user: AuthenticatedUser) -> dict:
    """Run one pipeline task independently of the browser connection."""
    from app.retrieval.pipeline import run_query_pipeline

    try:
        return await run_query_pipeline(query_id, user)
    except Exception:
        # The pipeline normally converts failures into a terminal database state.
        # Keep the task boundary defensive so an unexpected exception cannot
        # take down the SSE stream or cause a reconnect to rerun the query.
        logger.exception(
            "Query task failed", query_id=str(query_id), error_code="PIPELINE_TASK_ERROR"
        )
        factory = get_session_factory()
        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            result = await session.execute(
                select(QueryRun).where(
                    QueryRun.id == query_id,
                    QueryRun.user_id == user.user_id,
                    QueryRun.tenant_id == user.tenant_id,
                )
            )
            run = result.scalar_one_or_none()
            if run is not None and run.status not in _TERMINAL_STATUSES:
                run.status = "failed"
                run.error_code = "PIPELINE_TASK_ERROR"
                run.completed_at = datetime.now(UTC)
                await session.commit()
        return {"status": "failed", "query_id": str(query_id), "error_code": "PIPELINE_TASK_ERROR"}


async def _ensure_pipeline_started(query_id: UUID, user: AuthenticatedUser) -> None:
    """Start a query once and keep it alive when the SSE client reconnects."""
    async with _pipeline_tasks_lock:
        existing = _pipeline_tasks.get(query_id)
        if existing is not None and not existing.done():
            return

        status, _ = await _query_state(query_id, user)
        if status is None or status in _TERMINAL_STATUSES:
            return

        task = asyncio.create_task(_run_pipeline_once(query_id, user))
        _pipeline_tasks[query_id] = task

        def _forget(completed: asyncio.Task[dict]) -> None:
            if _pipeline_tasks.get(query_id) is completed:
                _pipeline_tasks.pop(query_id, None)

        task.add_done_callback(_forget)


async def create_query_run(
    user_id: UUID,
    tenant_id: UUID,
    query_text: str,
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
            status="created",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return {"id": run.id, "status": run.status}


async def stream_events(
    query_id: UUID,
    user: AuthenticatedUser,
) -> AsyncIterator[dict]:
    """Stream SSE progress events for a query.

    Events: query.authenticated, query.retrieving, query.reauthorising,
    query.reranking, query.generating, query.verifying, query.auditing,
    query.completed, query.partial, query.denied, query.failed
    """
    await _ensure_pipeline_started(query_id, user)
    last_progress_event: str | None = None

    while True:
        status, error_code = await _query_state(query_id, user)
        if status is None:
            yield {
                "event": "query.failed",
                "data": {
                    "queryId": str(query_id),
                    "errorCode": "SOURCE_OBJECT_NOT_FOUND",
                    "message": "The query could not be found.",
                },
            }
            return

        progress = _PROGRESS_BY_STATUS.get(status)
        if progress is not None and progress[0] != last_progress_event:
            event_type, message = progress
            yield {"event": event_type, "data": {"queryId": str(query_id), "message": message}}
            last_progress_event = event_type

        if status in _TERMINAL_STATUSES:
            terminal_event = {
                "completed": "query.completed",
                "partial": "query.partial",
                "denied": "query.denied",
                "failed": "query.failed",
            }[status]
            data = {"queryId": str(query_id)}
            if status == "failed":
                data["errorCode"] = error_code or "PIPELINE_ERROR"
                data["message"] = _failure_message(query_id, error_code)
            elif status == "denied" and error_code == _EMPTY_WORKSPACE_CODE:
                data["errorCode"] = error_code
                data["message"] = _EMPTY_WORKSPACE_MESSAGE
            yield {"event": terminal_event, "data": data}
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
            from fastapi import HTTPException, status

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
            "answer": answer,
            "claims": claims,
            "citations": citations,
            "partialSourceWarnings": partial_warnings,
            "errorCode": run.error_code,
            "errorMessage": (
                _failure_message(query_id, run.error_code) if run.status == "failed" else None
            ),
        }
