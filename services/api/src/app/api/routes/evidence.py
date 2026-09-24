"""Evidence API routes — citation reauthorisation endpoint.

GET /v1/evidence/{evidenceId}

Flow:
1. Authenticate enterprise user
2. Load evidence metadata WITHOUT exposing text
3. Ensure evidence belongs to user's tenant
4. Resolve current delegated provider credential
5. Perform live provider access check
6. If allowed → decrypt and return evidence span
7. If denied/unknown → return access-revoked without title or span
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies.auth import get_authenticated_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


class EvidenceResponse(BaseModel):
    evidenceId: str
    source: str
    title: str | None = None
    quotedSpan: str | None = None
    canonicalUrl: str | None = None


class EvidenceDeniedResponse(BaseModel):
    evidenceId: str
    status: str = "access_revoked"


@router.get("/evidence/{evidence_id}")
async def get_evidence(
    evidence_id: UUID,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> EvidenceResponse | EvidenceDeniedResponse:
    """Fetch evidence for a citation — reauthorises access (INV-006)."""
    from app.domain.evidence_service import get_evidence_with_reauth

    return await get_evidence_with_reauth(evidence_id, user)
