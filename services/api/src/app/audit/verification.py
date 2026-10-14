"""Audit chain verification — detects tampering via SHA-256 hash chain."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.canonicalise import GENESIS_MARKER, compute_event_hash
from app.observability.logging import get_logger
from app.persistence.models import AuditEvent

logger = get_logger(__name__)


async def verify_chain(
    session: AsyncSession,
    tenant_id: UUID,
) -> bool:
    """Verify the integrity of the audit hash chain for a tenant.

    event_hash_n = SHA-256(canonical_json(event_n) || previous_event_hash)
    First event uses fixed genesis marker.
    """
    stmt = (
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.sequence_no)
    )
    result = await session.execute(stmt)
    events = result.scalars().all()

    if not events:
        return True  # Empty chain is valid

    previous_hash = GENESIS_MARKER
    expected_sequence = 1
    for event in events:
        if event.sequence_no != expected_sequence or event.previous_hash != previous_hash:
            logger.error(
                "Audit chain verification failed — sequence or predecessor mismatch",
                event_id=str(event.id),
                sequence_no=event.sequence_no,
            )
            return False
        expected_hash = compute_event_hash(event, previous_hash)
        if event.event_hash != expected_hash:
            logger.error(
                "Audit chain verification failed — tampering detected",
                event_id=str(event.id),
                sequence_no=event.sequence_no,
                expected=expected_hash,
                actual=event.event_hash,
            )
            return False
        previous_hash = event.event_hash
        expected_sequence += 1

    logger.info("Audit chain verified", tenant_id=str(tenant_id), events=len(events))
    return True
