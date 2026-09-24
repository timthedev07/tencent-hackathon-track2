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
    accessDecisions: list[dict] = Field(default_factory=list)
    evidenceManifest: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    auditChainVerified: bool = True
    partialSources: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


@router.get("/audit/{query_id}", response_model=AuditResponse)
async def get_audit_capsule(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> AuditResponse:
    """Retrieve the audit capsule for a completed query (compliance role required)."""
    from app.domain.audit_service import get_audit_capsule

    return await get_audit_capsule(query_id, user)
