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

import json
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import select

from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.auth.oidc import AuthenticatedUser
from app.observability.logging import get_logger
from app.persistence.models import QueryRun
from app.persistence.session import get_session_factory, set_tenant_context

logger = get_logger(__name__)


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
    # Import here to avoid circular imports during scaffolding
    from app.retrieval.pipeline import run_query_pipeline

    events = [
        ("query.authenticated", "Identity verified"),
        ("query.retrieving", "Searching connected sources"),
        ("query.reauthorising", "Checking your current access"),
        ("query.reranking", "Ranking authorised evidence"),
        ("query.generating", "Generating answer"),
        ("query.verifying", "Verifying citations"),
        ("query.auditing", "Saving audit record"),
    ]

    for event_type, message in events:
        yield {"event": event_type, "data": {"queryId": str(query_id), "message": message}}

    # Run the actual pipeline
    result = await run_query_pipeline(query_id, user)

    terminal_event = "query.completed"
    if result.get("status") == "partial":
        terminal_event = "query.partial"
    elif result.get("status") == "denied":
        terminal_event = "query.denied"
    elif result.get("status") == "failed":
        terminal_event = "query.failed"

    yield {"event": terminal_event, "data": {"queryId": str(query_id)}}


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
        }
