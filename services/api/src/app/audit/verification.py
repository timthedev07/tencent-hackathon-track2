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
    return bool((await verify_chain_report(session, tenant_id))["valid"])


async def verify_chain_report(
    session: AsyncSession,
    tenant_id: UUID,
) -> dict[str, object]:
    """Verify the chain and identify the first invalid event, if any."""
    stmt = (
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.sequence_no)
    )
    result = await session.execute(stmt)
    events = result.scalars().all()

    if not events:
        return {"valid": True, "eventCount": 0, "firstInvalidEventId": None}

    previous_hash = GENESIS_MARKER
    expected_sequence = 1
    for event in events:
        if event.sequence_no != expected_sequence or event.previous_hash != previous_hash:
            logger.error(
                "Audit chain verification failed — sequence or predecessor mismatch",
                event_id=str(event.id),
                sequence_no=event.sequence_no,
            )
            return {
                "valid": False,
                "eventCount": len(events),
                "firstInvalidEventId": str(event.id),
                "firstInvalidSequence": event.sequence_no,
                "reason": "sequence_or_predecessor_mismatch",
            }
        expected_hash = compute_event_hash(event, previous_hash)
        if event.event_hash != expected_hash:
            logger.error(
                "Audit chain verification failed — tampering detected",
                event_id=str(event.id),
                sequence_no=event.sequence_no,
                expected=expected_hash,
                actual=event.event_hash,
            )
            return {
                "valid": False,
                "eventCount": len(events),
                "firstInvalidEventId": str(event.id),
                "firstInvalidSequence": event.sequence_no,
                "reason": "event_hash_mismatch",
            }
        previous_hash = event.event_hash
        expected_sequence += 1

    logger.info("Audit chain verified", tenant_id=str(tenant_id), events=len(events))
    return {
        "valid": True,
        "eventCount": len(events),
        "firstInvalidEventId": None,
        "chainHead": previous_hash,
    }
