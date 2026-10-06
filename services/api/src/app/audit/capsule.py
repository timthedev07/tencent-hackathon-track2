"""Evidence capsule builder (spec Section 31).

Assembles minimum audit fields for reconstruction. Quoted spans remain
encrypted; the capsule references ciphertext, never plaintext.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.observability.logging import get_logger
from app.persistence.models import (
    AccessDecision,
    AuditEvent,
    EvidenceCapsule,
    EvidenceItem,
    QueryRun,
)

logger = get_logger(__name__)


async def build_evidence_capsule(
    session: AsyncSession,
    query_id: UUID,
    tenant_id: UUID,
) -> dict[str, Any]:
    run = (
        await session.execute(
            select(QueryRun).where(QueryRun.id == query_id, QueryRun.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if run is None:
        return {}

    decisions = (
        (
            await session.execute(
                select(AccessDecision)
                .where(AccessDecision.query_id == query_id)
                .order_by(AccessDecision.access_checked_at)
            )
        )
        .scalars()
        .all()
    )

    evidence = (
        (
            await session.execute(
                select(EvidenceItem)
                .where(EvidenceItem.query_id == query_id)
                .order_by(EvidenceItem.rank)
            )
        )
        .scalars()
        .all()
    )

    audit_event = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.query_id == query_id)
            .order_by(AuditEvent.sequence_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    persisted_capsule = (
        await session.execute(select(EvidenceCapsule).where(EvidenceCapsule.query_id == query_id))
    ).scalar_one_or_none()

    cm = get_credential_manager()
    answer: dict[str, Any] | str | None = None
    if run.answer_ciphertext:
        answer_text = cm.decrypt_bytes(EncryptedPayload(ciphertext=run.answer_ciphertext)).decode(
            "utf-8"
        )
        try:
            answer = json.loads(answer_text)
        except json.JSONDecodeError:
            answer = answer_text

    acl_outcomes = [
        {
            "sourceObjectId": str(d.source_object_id),
            "source": d.source,
            "decision": d.decision,
            "reasonCode": d.reason_code,
            "accessCheckedAt": d.access_checked_at.isoformat() if d.access_checked_at else None,
        }
        for d in decisions
    ]

    evidence_manifest = [
        {
            "evidenceId": str(e.id),
            "source": e.source,
            "sourceRevision": e.source_revision,
            "objectVersionId": str(e.object_version_id) if e.object_version_id else None,
            "contentHash": e.content_hash,
            "rank": e.rank,
        }
        for e in evidence
    ]

    citations = [
        {
            "evidenceId": str(e.id),
            "source": e.source,
            "label": e.title_snapshot,
            "url": e.canonical_url,
        }
        for e in evidence
    ]

    partial_warnings: list[dict[str, Any]] = []
    if run.partial_sources:
        for src in run.partial_sources:
            partial_warnings.append({"source": src, "reason": "live_authorisation_timeout"})

    errors: list[dict[str, Any]] = []
    if run.error_code:
        errors.append({"code": run.error_code})

    logger.info("Evidence capsule built", query_id=str(query_id), evidence_count=len(evidence))
    return {
        "queryId": str(run.id),
        "tenantId": str(run.tenant_id),
        "userIdentifier": str(run.user_id),
        "timestamp": run.started_at.isoformat() if run.started_at else None,
        "asOf": run.as_of.isoformat() if run.as_of else None,
        "promptVersion": run.prompt_version,
        "policyVersion": run.policy_version,
        "modelVersion": run.model_version,
        "embeddingVersion": run.embedding_version,
        "rerankerVersion": run.reranker_version,
        "contextCompilerVersion": run.context_compiler_version,
        "authorisedContextHash": run.authorised_context_hash,
        "contextManifest": persisted_capsule.context_manifest if persisted_capsule else None,
        "candidateObjectIds": [str(d.source_object_id) for d in decisions],
        "accessDecisions": acl_outcomes,
        "authorisedEvidenceIds": [str(e.id) for e in evidence],
        "evidenceManifest": evidence_manifest,
        "citations": citations,
        "answer": answer,
        "latencies": {"totalMs": run.latency_ms},
        "partialSources": partial_warnings,
        "errors": errors,
        "auditChainEventId": str(audit_event.id) if audit_event else None,
        "queryStatus": run.status,
    }
