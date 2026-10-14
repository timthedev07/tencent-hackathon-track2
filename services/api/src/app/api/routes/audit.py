"""Compliance audit endpoint.

GET /v1/audit/queries
GET /v1/audit/{queryId}

Authorisation: enterprise auth + compliance role
- Lists tenant query history with pagination
- Requires an exact query ID for detailed audit capsules
- Returns the evidence capsule
- Returns audit-chain verification status
- Respects tenant isolation
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.dependencies.auth import get_compliance_user
from app.auth.oidc import AuthenticatedUser
from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.persistence.models import AuditEvent, EvidenceItem, QueryRun, User
from app.persistence.session import get_session_factory, set_tenant_context

router = APIRouter()


class AuditResponse(BaseModel):
    queryId: str
    answer: str | None = None
    timestamp: str | None = None
    userIdentifier: str | None = None
    queryStatus: str | None = None
    modelVersion: str | None = None
    promptVersion: str | None = None
    accessDecisions: list[dict] = Field(default_factory=list)
    evidenceManifest: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    auditChainVerified: bool = True
    partialSources: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class AuditHistoryItem(BaseModel):
    queryId: str
    queryText: str
    timestamp: str | None = None
    userIdentifier: str | None = None
    userDisplayName: str | None = None
    queryStatus: str
    evidenceCount: int = 0
    auditEventCount: int = 0


class AuditHistoryResponse(BaseModel):
    items: list[AuditHistoryItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    auditChainVerified: bool


@router.get("/audit/queries", response_model=AuditHistoryResponse)
async def list_audit_history(
    user: AuthenticatedUser = Depends(get_compliance_user),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AuditHistoryResponse:
    """List tenant query history for compliance users without exposing ciphertext."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)

        total = await session.scalar(
            select(func.count()).select_from(QueryRun).where(QueryRun.tenant_id == user.tenant_id)
        )
        rows = (
            await session.execute(
                select(QueryRun, User)
                .join(User, User.id == QueryRun.user_id)
                .where(QueryRun.tenant_id == user.tenant_id)
                .order_by(QueryRun.started_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

        query_ids = [run.id for run, _ in rows]
        evidence_counts: dict[str, int] = {}
        audit_event_counts: dict[str, int] = {}
        if query_ids:
            evidence_rows = await session.execute(
                select(EvidenceItem.query_id, func.count(EvidenceItem.id))
                .where(EvidenceItem.query_id.in_(query_ids))
                .group_by(EvidenceItem.query_id)
            )
            evidence_counts = {query_id: int(count) for query_id, count in evidence_rows.all()}

            audit_rows = await session.execute(
                select(AuditEvent.query_id, func.count(AuditEvent.id))
                .where(AuditEvent.query_id.in_(query_ids))
                .group_by(AuditEvent.query_id)
            )
            audit_event_counts = {query_id: int(count) for query_id, count in audit_rows.all()}

        credential_manager = get_credential_manager()
        items = []
        for run, query_user in rows:
            query_text = credential_manager.decrypt_bytes(
                EncryptedPayload(ciphertext=run.query_ciphertext)
            ).decode("utf-8")
            items.append(
                AuditHistoryItem(
                    queryId=str(run.id),
                    queryText=query_text,
                    timestamp=run.started_at.isoformat() if run.started_at else None,
                    userIdentifier=query_user.email or query_user.oidc_subject,
                    userDisplayName=query_user.display_name,
                    queryStatus=run.status,
                    evidenceCount=evidence_counts.get(run.id, 0),
                    auditEventCount=audit_event_counts.get(run.id, 0),
                )
            )

        from app.audit.verification import verify_chain

        return AuditHistoryResponse(
            items=items,
            total=int(total or 0),
            limit=limit,
            offset=offset,
            auditChainVerified=await verify_chain(session, user.tenant_id),
        )


@router.get("/audit/{query_id}", response_model=AuditResponse)
async def get_audit_capsule(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> AuditResponse:
    """Retrieve the audit capsule for a completed query (compliance role required)."""
    from app.domain.audit_service import get_audit_capsule

    return await get_audit_capsule(query_id, user)  # type: ignore[return-value]
