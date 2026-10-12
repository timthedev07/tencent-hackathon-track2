"""Identity link management.

Each user/source pair must be linked to an immutable provider subject.
An absent or ambiguous identity link means deny for that source (INV-002).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.types import Source
from app.observability.logging import get_logger
from app.persistence.models import IdentityLink, SourceConnection, User

logger = get_logger(__name__)


async def find_identity_link(
    session: AsyncSession,
    user_id: UUID,
    source: Source,
    as_of: datetime | None = None,
) -> IdentityLink | None:
    """Find a user's verified identity link for a given source.

    If missing or ambiguous, the source is denied (INV-002).
    """
    from app.retrieval.temporal import active_at, active_now

    temporal_filter = active_at(IdentityLink, as_of) if as_of else active_now(IdentityLink)
    stmt = (
        select(IdentityLink)
        .join(
            SourceConnection,
            IdentityLink.source_connection_id == SourceConnection.id,
        )
        .where(
            IdentityLink.user_id == user_id,
            SourceConnection.source == source.value,
            IdentityLink.status == "verified",
            temporal_filter,
        )
        .limit(2)
    )
    result = await session.execute(stmt)
    links = result.scalars().all()

    if len(links) == 0:
        logger.info(
            "Identity link missing — source will be denied",
            user_id=str(user_id),
            source=source.value,
        )
        return None

    if len(links) > 1:
        logger.warning(
            "Ambiguous identity links — source will be denied",
            user_id=str(user_id),
            source=source.value,
            count=len(links),
        )
        return None

    return links[0]


async def create_identity_link(
    session: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    source_connection_id: UUID | str,
    provider_subject_id: str,
    *,
    provider_account_type: str | None = None,
    verified_by: str = "oauth",
) -> IdentityLink:
    """Create and verify a new identity link."""
    link = IdentityLink(
        tenant_id=tenant_id,
        user_id=user_id,
        source_connection_id=source_connection_id,
        provider_subject_id=provider_subject_id,
        provider_account_type=provider_account_type,
        verified_at=datetime.now(UTC),
        verified_by=verified_by,
        status="verified",
        valid_from=datetime.now(UTC),
        observed_at=datetime.now(UTC),
    )
    session.add(link)
    await session.flush()
    logger.info(
        "Identity link created",
        user_id=str(user_id),
        source_connection_id=str(source_connection_id),
        provider_subject_id=provider_subject_id,
    )
    return link


async def is_compliance_user(session: AsyncSession, user_id: UUID) -> bool:
    """Check if a user has the compliance role."""
    stmt = select(User.is_compliance_user).where(User.id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() or False
