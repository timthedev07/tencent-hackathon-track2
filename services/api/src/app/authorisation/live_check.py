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
from app.persistence.models import AccessDecision, EncryptedCredential, SourceObject
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
        select(EncryptedCredential)
        .where(
            EncryptedCredential.source_connection_id == link.source_connection_id,
            EncryptedCredential.user_id == user.user_id,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    cred = result.scalar_one_or_none()
    if cred is None:
        return None
    cm = get_credential_manager()
    try:
        plaintext = cm.decrypt(
            EncryptedPayload(ciphertext=cred.ciphertext, key_version=cred.key_version)
        )
    except Exception:
        logger.debug("Credential decryption failed", exc_info=True)
        return None
    return DelegatedCredential(source=source, access_token=plaintext, expires_at=cred.expires_at)


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


async def check_live_access(
    session: AsyncSession,
    user: AuthenticatedUser,
    source: Source,
    source_object_id: UUID,
) -> AccessCheckResult:
    """Perform a live provider access check for a single object."""
    start = time.monotonic()
    credential = await _resolve_delegated_credential(session, user, source)
    if credential is None:
        return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="no_delegated_credential")

    obj_stmt = select(SourceObject.external_object_id).where(SourceObject.id == source_object_id)
    external_object_id = (await session.execute(obj_stmt)).scalar_one_or_none()
    if external_object_id is None:
        return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="object_not_found")

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
    stmt = select(SourceObject.id, SourceObject.external_object_id).where(
        SourceObject.id.in_(object_ids)
    )
    result = await session.execute(stmt)
    ext_id_map: dict[UUID, str] = {row[0]: row[1] for row in result.all()}

    sources: set[Source] = {src for _, src in objects}
    credentials: dict[Source, DelegatedCredential | None] = {}
    for src in sources:
        credentials[src] = await _resolve_delegated_credential(session, user, src)

    results: dict[UUID, AccessCheckResult] = {}
    tasks: list[tuple[UUID, asyncio.Task[tuple[UUID, AccessCheckResult]]]] = []

    try:
        async with asyncio.timeout(deadline / 1000):
            for source_object_id, source in objects:
                external_id = ext_id_map.get(source_object_id)
                if external_id is None:
                    results[source_object_id] = AccessCheckResult(
                        outcome=AccessOutcome.DENY, reason_code="object_not_found"
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
