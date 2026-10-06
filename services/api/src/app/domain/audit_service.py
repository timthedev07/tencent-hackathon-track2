"""Audit service — compliance audit capsule retrieval."""

from __future__ import annotations

from uuid import UUID

from app.audit.capsule import build_evidence_capsule
from app.audit.checkpoint import create_checkpoint, verify_latest_checkpoint
from app.audit.verification import verify_chain, verify_chain_report
from app.auth.oidc import AuthenticatedUser
from app.observability.logging import get_logger
from app.persistence.session import get_session_factory, set_tenant_context

logger = get_logger(__name__)


async def get_audit_capsule(
    query_id: UUID,
    user: AuthenticatedUser,
) -> dict:
    """Retrieve the audit capsule for a completed query (compliance role)."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)

        capsule = await build_evidence_capsule(session, query_id, user.tenant_id)

        if not capsule:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "SOURCE_OBJECT_NOT_FOUND",
                        "message": "Query not found.",
                    }
                },
            )

        chain_verified = await verify_chain(session, user.tenant_id)
        capsule["auditChainVerified"] = chain_verified
        capsule["checkpointVerification"] = await verify_latest_checkpoint(session, user.tenant_id)

        return capsule


async def verify_audit_integrity(user: AuthenticatedUser) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        return {
            "tenantId": str(user.tenant_id),
            "chain": await verify_chain_report(session, user.tenant_id),
            "checkpoint": await verify_latest_checkpoint(session, user.tenant_id),
        }


async def write_audit_checkpoint(user: AuthenticatedUser) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        result = await create_checkpoint(session, user.tenant_id)
        await session.commit()
        return result
