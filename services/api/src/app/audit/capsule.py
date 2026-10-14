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
from app.persistence.models import AccessDecision, AuditEvent, EvidenceItem, QueryRun, SourceObject

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

    evidence_rows = (
        await session.execute(
            select(EvidenceItem, SourceObject.external_object_id)
            .join(SourceObject, EvidenceItem.source_object_id == SourceObject.id)
            .where(EvidenceItem.query_id == query_id)
            .order_by(EvidenceItem.rank)
        )
    ).all()
    evidence = [item for item, _ in evidence_rows]

    audit_event = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.query_id == query_id)
            .order_by(AuditEvent.sequence_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    cm = get_credential_manager()
    answer_text: str | None = None
    if run.answer_ciphertext:
        decrypted_answer = cm.decrypt_bytes(
            EncryptedPayload(ciphertext=run.answer_ciphertext)
        ).decode("utf-8")
        try:
            answer_payload = json.loads(decrypted_answer)
            answer_text = answer_payload.get("answer", decrypted_answer)
        except (json.JSONDecodeError, AttributeError):
            # Preserve compatibility with older rows that stored plain text.
            answer_text = decrypted_answer

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
            "rank": e.rank,
        }
        for e in evidence
    ]

    citations = [
        {
            "evidenceId": str(evidence_item.id),
            "referenceId": (
                f"evi_{str(evidence_item.model_evidence_id).replace('-', '')[:12]}"
                if evidence_item.model_evidence_id
                else None
            ),
            "itemKey": (
                str(external_object_id) if evidence_item.source == "jira" else None
            ),
            "source": evidence_item.source,
            "label": evidence_item.title_snapshot,
            "url": evidence_item.canonical_url,
        }
        for evidence_item, external_object_id in evidence_rows
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
        "promptVersion": run.prompt_version,
        "modelVersion": run.model_version,
        "rerankerVersion": run.reranker_version,
        "candidateObjectIds": [str(d.source_object_id) for d in decisions],
        "accessDecisions": acl_outcomes,
        "authorisedEvidenceIds": [str(e.id) for e in evidence],
        "evidenceManifest": evidence_manifest,
        "citations": citations,
        "answer": answer_text,
        "latencies": {"totalMs": run.latency_ms},
        "partialSourceWarnings": partial_warnings,
        "errors": errors,
        "auditChainEventId": str(audit_event.id) if audit_event else None,
        "queryStatus": run.status,
    }
