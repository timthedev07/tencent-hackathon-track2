"""Compliance audit endpoint.

GET /v1/audit/{queryId}

Authorisation: enterprise auth + compliance role
- Does NOT expose arbitrary search
- Requires an exact query ID
- Returns the evidence capsule
- Returns audit-chain verification status
- Respects tenant isolation
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_compliance_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


class AuditResponse(BaseModel):
    queryId: str
    timestamp: str | None = None
    userIdentifier: str | None = None
    queryStatus: str | None = None
    modelVersion: str | None = None
    promptVersion: str | None = None
    asOf: str | None = None
    policyVersion: str | None = None
    embeddingVersion: str | None = None
    rerankerVersion: str | None = None
    contextCompilerVersion: str | None = None
    authorisedContextHash: str | None = None
    contextManifest: dict | None = None
    answer: dict | str | None = None
    latencies: dict | None = None
    auditChainEventId: str | None = None
    accessDecisions: list[dict] = Field(default_factory=list)
    evidenceManifest: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    auditChainVerified: bool = True
    checkpointVerification: dict | None = None
    partialSources: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


@router.post("/audit/verify-chain")
async def verify_audit_chain(
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> dict:
    """Recompute the tenant chain and verify the latest exported checkpoint."""
    from app.domain.audit_service import verify_audit_integrity

    return await verify_audit_integrity(user)


@router.post("/audit/checkpoints")
async def create_audit_checkpoint(
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> dict:
    """Sign and export the current tenant chain head."""
    from app.domain.audit_service import write_audit_checkpoint

    return await write_audit_checkpoint(user)


@router.get("/audit/{query_id}", response_model=AuditResponse)
async def get_audit_capsule(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> AuditResponse:
    """Retrieve the audit capsule for a completed query (compliance role required)."""
    from app.domain.audit_service import get_audit_capsule

    return await get_audit_capsule(query_id, user)  # type: ignore[return-value]
