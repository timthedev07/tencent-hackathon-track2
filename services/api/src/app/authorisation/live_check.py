"""Live access check — the authoritative per-query authorisation.

INV-003: Positive access is query-scoped — never reused.
INV-004: No service-account fallback.
Per-provider semaphores (8 concurrent). Strict deadline (~1.5s).
NEVER assume access (INV-002). Never send object text to the checker.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.auth.identities import find_identity_link
from app.auth.oidc import AuthenticatedUser
from app.connectors.protocol import Connector
from app.domain.types import (
    AccessCheckResult,
    AccessOutcome,
    DelegatedCredential,
    Source,
)
from app.observability.logging import get_logger
from app.persistence.models import (
    AccessDecision,
    AclGrant,
    EncryptedCredential,
    IdentityLink,
    Principal,
    SourceConnection,
    SourceObject,
)
from app.retrieval.temporal import active_now
from app.settings import get_settings

logger = get_logger(__name__)

_semaphores: dict[Source, asyncio.Semaphore] = {}


def _get_semaphore(source: Source) -> asyncio.Semaphore:
    if source not in _semaphores:
        _semaphores[source] = asyncio.Semaphore(get_settings().PROVIDER_CONCURRENCY_LIMIT)
    return _semaphores[source]


async def _resolve_delegated_credential(
    session: AsyncSession, user: AuthenticatedUser, source: Source
) -> DelegatedCredential | None:
    link = await find_identity_link(session, user.user_id, source)
    if link is None:
        return None
    stmt = (
        select(EncryptedCredential, SourceConnection)
        .join(SourceConnection, EncryptedCredential.source_connection_id == SourceConnection.id)
        .where(
            EncryptedCredential.source_connection_id == link.source_connection_id,
            EncryptedCredential.user_id == user.user_id,
            SourceConnection.status == "connected",
        )
        .order_by(EncryptedCredential.updated_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        return None
    cred, connection = row
    cm = get_credential_manager()
    try:
        plaintext = cm.decrypt(
            EncryptedPayload(ciphertext=cred.ciphertext, key_version=cred.key_version)
        )
    except Exception:
        logger.debug("Credential decryption failed", exc_info=True)
        return None
    from app.connectors.oauth import refresh_access_token, unpack_tokens

    token_data = unpack_tokens(plaintext)
    access_token = str(token_data.get("access_token") or "")
    refresh_token = token_data.get("refresh_token")
    if (
        cred.credential_type == "oauth_refresh"
        and token_data.get("refresh_token") is None
        and not plaintext.lstrip().startswith("{")
    ):
        access_token = ""
        refresh_token = plaintext
    expires_at = cred.expires_at
    payload_expires_at = token_data.get("expires_at")
    if isinstance(payload_expires_at, str):
        try:
            expires_at = datetime.fromisoformat(payload_expires_at)
        except ValueError:
            expires_at = cred.expires_at
    if (not access_token or (expires_at and expires_at <= datetime.now(UTC))) and refresh_token:
        try:
            refreshed = await refresh_access_token(source, str(refresh_token))
        except ValueError:
            return None
        access_token = refreshed["access_token"]
        refresh_token = refreshed.get("refresh_token")
        expires_at = refreshed.get("expires_at")
        # This request-scoped check never commits unrelated state.
    if not access_token:
        return None
    return DelegatedCredential(
        source=source,
        access_token=access_token,
        refresh_token=str(refresh_token) if refresh_token else None,
        expires_at=expires_at,
        provider_metadata={
            "workspace_id": connection.external_workspace_id,
            "cloud_id": connection.external_workspace_id,
            "provider_subject_id": link.provider_subject_id,
        },
    )


async def _get_connector(source: Source) -> Connector | None:
    """Get the connector for a given source.

    Returns None if the connector is not yet implemented.
    The pipeline handles this by marking sources as unavailable.
    """
    if source == Source.CONFLUENCE:
        from app.connectors.confluence import ConfluenceConnector

        return ConfluenceConnector()  # type: ignore[return-value]
    if source == Source.JIRA:
        from app.connectors.jira import JiraConnector

        return JiraConnector()  # type: ignore[return-value]
    if source == Source.SLACK:
        from app.connectors.slack import SlackConnector

        return SlackConnector()  # type: ignore[return-value]
    if source == Source.GOOGLE_DRIVE:
        from app.connectors.google_drive import GoogleDriveConnector

        return GoogleDriveConnector()  # type: ignore[return-value]
    return None


async def _check_fixture_access(
    session: AsyncSession,
    user: AuthenticatedUser,
    source_object_id: UUID,
    source_connection_id: UUID,
) -> AccessCheckResult:
    """Simulate an authoritative provider check from mutable fixture state."""
    link_stmt = (
        select(IdentityLink)
        .where(
            IdentityLink.user_id == user.user_id,
            IdentityLink.source_connection_id == source_connection_id,
            IdentityLink.status == "verified",
            active_now(IdentityLink),
        )
        .limit(2)
    )
    links = (await session.execute(link_stmt)).scalars().all()
    if len(links) != 1:
        return AccessCheckResult(
            outcome=AccessOutcome.DENY,
            reason_code="fixture_identity_missing_or_ambiguous",
        )

    grant_stmt = (
        select(AclGrant.grant_effect)
        .join(Principal, AclGrant.principal_id == Principal.id)
        .where(
            AclGrant.source_object_id == source_object_id,
            Principal.source_connection_id == source_connection_id,
            Principal.provider_principal_id == links[0].provider_subject_id,
            active_now(AclGrant),
        )
    )
    effects = set((await session.execute(grant_stmt)).scalars().all())
    if "deny" in effects:
        return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="fixture_acl_deny")
    if "allow" in effects:
        return AccessCheckResult(outcome=AccessOutcome.ALLOW, reason_code="fixture_acl_allow")
    return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="fixture_acl_no_grant")


async def check_live_access(  # noqa: PLR0911
    session: AsyncSession,
    user: AuthenticatedUser,
    source: Source,
    source_object_id: UUID,
) -> AccessCheckResult:
    """Perform a live provider access check for a single object."""
    start = time.monotonic()
    object_stmt = (
        select(
            SourceObject.external_object_id,
            SourceObject.source_connection_id,
            SourceConnection.mode,
        )
        .join(SourceConnection, SourceObject.source_connection_id == SourceConnection.id)
        .where(SourceObject.id == source_object_id)
    )
    object_row = (await session.execute(object_stmt)).first()
    if object_row is None:
        return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="object_not_found")
    external_object_id, source_connection_id, connection_mode = object_row
    if connection_mode == "fixture":
        return await _check_fixture_access(
            session,
            user,
            source_object_id,
            source_connection_id,
        )

    credential = await _resolve_delegated_credential(session, user, source)
    if credential is None:
        return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="no_delegated_credential")
    if credential.expires_at is not None and credential.expires_at <= datetime.now(UTC):
        return AccessCheckResult(
            outcome=AccessOutcome.EXPIRED_CREDENTIAL,
            reason_code="credential_expired",
        )

    connector = await _get_connector(source)
    if connector is None:
        return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="connector_unavailable")

    sem = _get_semaphore(source)
    try:
        async with sem:
            result = await connector.check_access(
                user_credential=credential, external_object_id=external_object_id
            )
    except TimeoutError:
        return AccessCheckResult(outcome=AccessOutcome.TIMEOUT, reason_code="provider_timeout")
    except Exception as exc:
        logger.warning("Live access check failed", error=str(exc))
        return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="check_error")

    latency_ms = int((time.monotonic() - start) * 1000)
    return AccessCheckResult(
        outcome=result.outcome,
        reason_code=result.reason_code,
        provider_request_id=result.provider_request_id,
        latency_ms=latency_ms,
    )


async def check_live_access_batch(
    session: AsyncSession,
    user: AuthenticatedUser,
    objects: list[tuple[UUID, Source]],
    deadline_ms: int | None = None,
) -> dict[UUID, AccessCheckResult]:
    """Perform live access checks for a batch with deadline."""
    settings = get_settings()
    deadline = deadline_ms or settings.QUERY_AUTH_DEADLINE_MS

    object_ids = [obj_id for obj_id, _ in objects]
    stmt = (
        select(
            SourceObject.id,
            SourceObject.external_object_id,
            SourceObject.source_connection_id,
            SourceConnection.mode,
        )
        .join(SourceConnection, SourceObject.source_connection_id == SourceConnection.id)
        .where(SourceObject.id.in_(object_ids))
    )
    result = await session.execute(stmt)
    object_map: dict[UUID, tuple[str, UUID, str]] = {
        row[0]: (row[1], row[2], row[3]) for row in result.all()
    }

    sources: set[Source] = {
        src
        for object_id, src in objects
        if object_id in object_map and object_map[object_id][2] != "fixture"
    }
    credentials: dict[Source, DelegatedCredential | None] = {}
    for src in sources:
        credentials[src] = await _resolve_delegated_credential(session, user, src)

    results: dict[UUID, AccessCheckResult] = {}
    tasks: list[tuple[UUID, asyncio.Task[tuple[UUID, AccessCheckResult]]]] = []

    try:
        async with asyncio.timeout(deadline / 1000):
            for source_object_id, source in objects:
                object_data = object_map.get(source_object_id)
                if object_data is None:
                    results[source_object_id] = AccessCheckResult(
                        outcome=AccessOutcome.DENY, reason_code="object_not_found"
                    )
                    continue
                external_id, source_connection_id, connection_mode = object_data
                if connection_mode == "fixture":
                    results[source_object_id] = await _check_fixture_access(
                        session,
                        user,
                        source_object_id,
                        source_connection_id,
                    )
                    continue
                cred = credentials.get(source)
                if cred is None:
                    results[source_object_id] = AccessCheckResult(
                        outcome=AccessOutcome.DENY, reason_code="no_delegated_credential"
                    )
                    continue
                task = asyncio.create_task(
                    _check_single(user, source, source_object_id, external_id, cred, deadline)
                )
                tasks.append((source_object_id, task))

            for source_object_id, task in tasks:
                try:
                    _, check_result = await task
                    results[source_object_id] = check_result
                except TimeoutError:
                    results[source_object_id] = AccessCheckResult(
                        outcome=AccessOutcome.TIMEOUT, reason_code="batch_deadline_exceeded"
                    )
    except TimeoutError:
        for source_object_id, task in tasks:
            if source_object_id not in results:
                if not task.done():
                    task.cancel()
                results[source_object_id] = AccessCheckResult(
                    outcome=AccessOutcome.TIMEOUT, reason_code="overall_deadline_exceeded"
                )

    return results


async def _check_single(
    user: AuthenticatedUser,
    source: Source,
    source_object_id: UUID,
    external_id: str,
    credential: DelegatedCredential,
    deadline_ms: int,
) -> tuple[UUID, AccessCheckResult]:
    settings = get_settings()
    if credential.expires_at is not None and credential.expires_at <= datetime.now(UTC):
        return source_object_id, AccessCheckResult(
            outcome=AccessOutcome.EXPIRED_CREDENTIAL,
            reason_code="credential_expired",
        )
    subdeadline = min(deadline_ms, settings.QUERY_AUTH_DEADLINE_MS)
    connector = await _get_connector(source)
    if connector is None:
        return source_object_id, AccessCheckResult(
            outcome=AccessOutcome.UNKNOWN, reason_code="connector_unavailable"
        )
    sem = _get_semaphore(source)
    start = time.monotonic()
    try:
        async with asyncio.timeout(subdeadline / 1000):
            async with sem:
                result = await connector.check_access(
                    user_credential=credential, external_object_id=external_id
                )
    except TimeoutError:
        return source_object_id, AccessCheckResult(
            outcome=AccessOutcome.TIMEOUT, reason_code="provider_timeout"
        )
    except Exception as exc:
        logger.warning("Live access check failed", error=str(exc))
        return source_object_id, AccessCheckResult(
            outcome=AccessOutcome.UNKNOWN, reason_code="check_error"
        )
    latency_ms = int((time.monotonic() - start) * 1000)
    return source_object_id, AccessCheckResult(
        outcome=result.outcome,
        reason_code=result.reason_code,
        provider_request_id=result.provider_request_id,
        latency_ms=latency_ms,
    )


async def persist_access_decisions(
    session: AsyncSession,
    query_id: UUID,
    tenant_id: UUID,
    decisions: dict[UUID, AccessCheckResult],
    object_source_map: dict[UUID, Source],
) -> None:
    """Persist access decisions — both allow and deny."""
    for source_object_id, result in decisions.items():
        source = object_source_map.get(source_object_id)
        if source is None:
            continue
        decision = AccessDecision(
            query_id=query_id,
            tenant_id=tenant_id,
            source_object_id=source_object_id,
            source=source.value,
            decision=result.outcome.value,
            reason_code=result.reason_code,
            access_checked_at=datetime.now(UTC),
            provider_latency_ms=result.latency_ms,
            provider_request_id=result.provider_request_id,
        )
        session.add(decision)
    await session.flush()
    logger.info("Access decisions persisted", query_id=str(query_id), count=len(decisions))
