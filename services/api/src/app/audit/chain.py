"""Audit chain management — append events and manage the hash chain."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.canonicalise import GENESIS_MARKER, compute_event_hash
from app.observability.logging import get_logger
from app.persistence.models import AuditEvent, Tenant

logger = get_logger(__name__)


async def append_event(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: str,
    canonical_payload: dict[str, Any],
    query_id: UUID | None = None,
) -> AuditEvent:
    """Append a new event to the audit chain.

    Computes the hash chain: event_hash = SHA-256(canonical_json(event) || previous_hash)
    First event uses fixed genesis marker.
    """
    # Serialise appends for a tenant.  A max(sequence_no) calculation without
    # a lock allows concurrent queries to produce duplicate sequence numbers
    # and divergent chain roots.
    await session.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())

    # Get the next sequence number
    stmt = select(func.max(AuditEvent.sequence_no)).where(AuditEvent.tenant_id == tenant_id)
    result = await session.execute(stmt)
    max_seq = result.scalar_one()
    sequence_no = (max_seq or 0) + 1

    # Get previous hash
    prev_stmt = (
        select(AuditEvent.event_hash)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.sequence_no.desc())
        .limit(1)
    )
    prev_result = await session.execute(prev_stmt)
    previous_hash = prev_result.scalar_one_or_none() or GENESIS_MARKER

    # Create the event
    event = AuditEvent(
        tenant_id=tenant_id,
        query_id=query_id,
        sequence_no=sequence_no,
        event_type=event_type,
        canonical_payload=canonical_payload,
        previous_hash=previous_hash,
        created_at=datetime.now(UTC),
    )
    # Compute hash
    event.event_hash = compute_event_hash(event, previous_hash)

    session.add(event)
    await session.flush()

    logger.info(
        "Audit event appended",
        event_id=str(event.id),
        sequence_no=sequence_no,
        event_type=event_type,
    )
    return event
