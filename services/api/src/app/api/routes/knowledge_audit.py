"""Knowledge Auditor API."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_compliance_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


@router.get("/knowledge-audit/findings")
async def get_knowledge_findings(
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> dict:
    from app.domain.knowledge_audit_service import list_knowledge_findings

    return await list_knowledge_findings(user)
