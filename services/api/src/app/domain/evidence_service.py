"""Evidence service — citation reauthorisation (INV-006) and persistence.

Opening a citation triggers a fresh source authorisation check.
If access has been revoked, the evidence text, title, and sensitive
metadata must NOT be revealed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.auth.oidc import AuthenticatedUser
from app.observability.logging import get_logger
from app.persistence.models import EvidenceItem, QueryRun
from app.persistence.session import get_session_factory, set_tenant_context

logger = get_logger(__name__)


def encrypt_quoted_span(plaintext: str) -> bytes:
    """Encrypt a quoted evidence span for at-rest persistence."""
    cm = get_credential_manager()
    payload = cm.encrypt_bytes(plaintext.encode("utf-8"))
    return payload.ciphertext


async def get_evidence_with_reauth(
    evidence_id: UUID,
    user: AuthenticatedUser,
) -> dict:
    """Fetch evidence with live reauthorisation (INV-006)."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)

        evidence = (
            await session.execute(select(EvidenceItem).where(EvidenceItem.id == evidence_id))
        ).scalar_one_or_none()

        if evidence is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "SOURCE_OBJECT_NOT_FOUND",
                        "message": "Evidence not found.",
                    }
                },
            )

        query_result = (
            await session.execute(
                select(QueryRun).where(
                    QueryRun.id == evidence.query_id,
                    QueryRun.user_id == user.user_id,
                )
            )
        ).scalar_one_or_none()

        if query_result is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "SOURCE_ACCESS_FORBIDDEN",
                        "message": "Access denied.",
                    }
                },
            )

        from app.auth.identities import find_identity_link
        from app.domain.types import Source

        source = Source(evidence.source)
        link = await find_identity_link(session, user.user_id, source)

        if link is None:
            logger.info("Evidence denied — identity link missing", evidence_id=str(evidence_id))
            return {"evidenceId": str(evidence_id), "status": "access_revoked"}

        from app.authorisation.live_check import check_live_access

        access_result = await check_live_access(
            session=session,
            user=user,
            source=source,
            source_object_id=UUID(evidence.source_object_id),
        )

        if access_result.outcome.value != "allow":
            logger.info(
                "Evidence access revoked on recheck",
                evidence_id=str(evidence_id),
                outcome=access_result.outcome.value,
            )
            return {"evidenceId": str(evidence_id), "status": "access_revoked"}

        cm = get_credential_manager()
        quoted_span: str | None = None
        if evidence.quoted_span_ciphertext:
            quoted_span = cm.decrypt_bytes(
                EncryptedPayload(ciphertext=evidence.quoted_span_ciphertext)
            ).decode("utf-8")

        return {
            "evidenceId": str(evidence_id),
            "source": evidence.source,
            "title": evidence.title_snapshot,
            "quotedSpan": quoted_span,
            "canonicalUrl": evidence.canonical_url,
        }
