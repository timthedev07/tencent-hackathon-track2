"""Local ACL projection — optimisation to reject obvious non-matches.

Local allow NEVER skips the live check.
Local deny MAY reject early where semantics are certain.
Ambiguous local ACL: may proceed to live check if safe, otherwise deny.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identities import find_identity_link
from app.domain.types import AccessOutcome, Source
from app.observability.logging import get_logger
from app.persistence.models import AclGrant, IdentityLink, Principal, SourceConnection

logger = get_logger(__name__)


class LocalAclVerdict:
    def __init__(self, outcome: AccessOutcome, reason: str = "") -> None:
        self.outcome = outcome
        self.reason = reason

    @property
    def should_proceed_to_live_check(self) -> bool:
        return self.outcome != AccessOutcome.DENY


async def check_local_acl(
    session: AsyncSession,
    user_id: UUID,
    source: Source,
    source_object_id: UUID,
) -> LocalAclVerdict:
    link = await find_identity_link(session, user_id, source)
    if link is None:
        return LocalAclVerdict(AccessOutcome.DENY, "identity_link_missing")

    stmt = (
        select(AclGrant)
        .join(Principal, AclGrant.principal_id == Principal.id)
        .join(
            SourceConnection,
            Principal.source_connection_id == SourceConnection.id,
        )
        .where(
            AclGrant.source_object_id == source_object_id,
            AclGrant.grant_effect == "allow",
            SourceConnection.source == source.value,
            Principal.provider_principal_id == link.provider_subject_id,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        return LocalAclVerdict(AccessOutcome.ALLOW, "local_acl_allow")

    deny_stmt = (
        select(AclGrant)
        .join(Principal, AclGrant.principal_id == Principal.id)
        .join(
            SourceConnection,
            Principal.source_connection_id == SourceConnection.id,
        )
        .where(
            AclGrant.source_object_id == source_object_id,
            AclGrant.grant_effect == "deny",
            SourceConnection.source == source.value,
            Principal.provider_principal_id == link.provider_subject_id,
        )
        .limit(1)
    )
    deny_result = await session.execute(deny_stmt)
    if deny_result.scalar_one_or_none() is not None:
        return LocalAclVerdict(AccessOutcome.DENY, "local_acl_deny")

    return LocalAclVerdict(AccessOutcome.UNKNOWN, "local_acl_no_grant")


async def batch_check_local_acl(
    session: AsyncSession,
    user_id: UUID,
    candidates: list[tuple[UUID, Source]],
) -> dict[UUID, LocalAclVerdict]:
    sources: set[Source] = {src for _, src in candidates}
    identity_links: dict[Source, IdentityLink | None] = {}

    for src in sources:
        identity_links[src] = await find_identity_link(session, user_id, src)

    results: dict[UUID, LocalAclVerdict] = {}

    for source_object_id, source in candidates:
        link = identity_links.get(source)
        if link is None:
            results[source_object_id] = LocalAclVerdict(AccessOutcome.DENY, "identity_link_missing")
            continue

        allow_stmt = (
            select(AclGrant)
            .join(Principal, AclGrant.principal_id == Principal.id)
            .join(
                SourceConnection,
                Principal.source_connection_id == SourceConnection.id,
            )
            .where(
                AclGrant.source_object_id == source_object_id,
                AclGrant.grant_effect == "allow",
                SourceConnection.source == source.value,
                Principal.provider_principal_id == link.provider_subject_id,
            )
            .limit(1)
        )
        allow_result = await session.execute(allow_stmt)
        if allow_result.scalar_one_or_none() is not None:
            results[source_object_id] = LocalAclVerdict(AccessOutcome.ALLOW, "local_acl_allow")
            continue

        deny_stmt = (
            select(AclGrant)
            .join(Principal, AclGrant.principal_id == Principal.id)
            .join(
                SourceConnection,
                Principal.source_connection_id == SourceConnection.id,
            )
            .where(
                AclGrant.source_object_id == source_object_id,
                AclGrant.grant_effect == "deny",
                SourceConnection.source == source.value,
                Principal.provider_principal_id == link.provider_subject_id,
            )
            .limit(1)
        )
        deny_result = await session.execute(deny_stmt)
        if deny_result.scalar_one_or_none() is not None:
            results[source_object_id] = LocalAclVerdict(AccessOutcome.DENY, "local_acl_deny")
        else:
            results[source_object_id] = LocalAclVerdict(AccessOutcome.UNKNOWN, "local_acl_no_grant")

    return results
