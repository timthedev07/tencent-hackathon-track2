"""ACL resolution task — fetch and persist ACL grants."""

from __future__ import annotations

from typing import Any

from app.connectors.errors import ConnectorError, RateLimitedError, TransientError
from app.domain.types import ConnectorOutcome, IngestionStatus
from app.observability.logging import get_logger
from app.persistence.models import AclGrant, ObjectVersion, Principal, SourceObject
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import delete, select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[misc]
    name="worker.tasks.acl.resolve_acl",
    autoretry_for=(RateLimitedError, TransientError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def resolve_acl(message: dict[str, Any]) -> dict[str, Any]:
    """Resolve ACL grants for an object and enqueue chunking."""
    from worker.async_runner import run_async

    return run_async(_resolve_acl(message))


async def _resolve_acl(message: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID

    from worker.tasks.ingestion_helpers import (
        connector_for,
        load_ingestion_context,
        validate_message,
    )

    validate_message(message)
    tenant_id = _required(message, "tenantId")
    connection_id = _required(message, "connectionId")
    object_id = _required(message, "externalObjectId")
    version_id = _required(message, "objectVersionId")
    user_id = _optional_string(message, "userId")
    run_id = _optional_string(message, "backfillRunId")
    source = str(message.get("source", "unknown"))
    logger.info("Resolving ACL", source=source)
    try:
        context = await load_ingestion_context(tenant_id, connection_id, user_id)
    except ValueError as exc:
        logger.warning("Could not load ACL ingestion context", source=source, error=str(exc))
        await _quarantine(tenant_id, version_id)
        await _complete_run_item(tenant_id, run_id, failed=True, failure_message=str(exc))
        return {"status": "failed", "source": source}
    try:
        result = await connector_for(context.connection.source).fetch_acl(
            context.connection,
            object_id,
            context.credential,
        )
    except ConnectorError as exc:
        if exc.outcome in {ConnectorOutcome.RATE_LIMITED, ConnectorOutcome.TRANSIENT_ERROR}:
            raise
        await _quarantine(tenant_id, version_id)
        await _complete_run_item(tenant_id, run_id, failed=True, failure_message=str(exc))
        return {"status": "quarantined", "source": source}
    if result.outcome != ConnectorOutcome.SUCCESS:
        await _quarantine(tenant_id, version_id)
        await _complete_run_item(
            tenant_id,
            run_id,
            failed=True,
            failure_message=f"ACL_{result.outcome.value.upper()}",
        )
        return {"status": "quarantined", "source": source}

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, UUID(tenant_id))
        version = await session.scalar(
            select(ObjectVersion).where(
                ObjectVersion.id == UUID(version_id),
                ObjectVersion.tenant_id == UUID(tenant_id),
            )
        )
        if version is None:
            await _complete_run_item(
                tenant_id, run_id, failed=True, failure_message="SOURCE_VERSION_MISSING"
            )
            return {"status": "missing", "source": source}
        source_object = await session.scalar(
            select(SourceObject).where(
                SourceObject.id == version.source_object_id,
                SourceObject.tenant_id == UUID(tenant_id),
            )
        )
        if source_object is None:
            await _complete_run_item(
                tenant_id, run_id, failed=True, failure_message="SOURCE_OBJECT_MISSING"
            )
            return {"status": "missing", "source": source}
        await session.execute(delete(AclGrant).where(AclGrant.source_object_id == source_object.id))
        revision = str(message.get("sourceRevision") or version.source_revision)
        for grant in result.grants:
            provider_id = grant.get("provider_principal_id")
            if not provider_id:
                continue
            principal_type = str(grant.get("principal_type") or "user")
            principal = await session.scalar(
                select(Principal).where(
                    Principal.source_connection_id == UUID(connection_id),
                    Principal.principal_type == principal_type,
                    Principal.provider_principal_id == str(provider_id),
                )
            )
            if principal is None:
                principal = Principal(
                    tenant_id=UUID(tenant_id),
                    source_connection_id=UUID(connection_id),
                    principal_type=principal_type,
                    provider_principal_id=str(provider_id),
                    display_name=(
                        str(grant["display_name"])
                        if grant.get("display_name")
                        else None
                    ),
                )
                session.add(principal)
                await session.flush()
            session.add(
                AclGrant(
                    tenant_id=UUID(tenant_id),
                    source_object_id=source_object.id,
                    principal_id=principal.id,
                    permission=str(grant.get("permission") or "read"),
                    grant_effect=str(grant.get("grant_effect") or "allow"),
                    inheritance_path=(
                        grant.get("inheritance_path")
                        if isinstance(grant.get("inheritance_path"), dict)
                        else None
                    ),
                    source_revision=revision,
                )
            )
        version.ingestion_status = IngestionStatus.ACL_RESOLVED.value
        await session.commit()

    from worker.tasks.chunk import chunk_object

    chunk_object.delay(dict(message))  # type: ignore[union-attr]
    return {"status": "acl_resolved", "source": source}


def _required(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"INGESTION_MESSAGE_{key.upper()}_MISSING")
    return value


def _optional_string(message: dict[str, Any], key: str) -> str | None:
    value = message.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"INGESTION_MESSAGE_{key.upper()}_INVALID")
    return value


async def _quarantine(tenant_id: str, version_id: str) -> None:
    from uuid import UUID

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, UUID(tenant_id))
        version = await session.scalar(
            select(ObjectVersion).where(
                ObjectVersion.id == UUID(version_id),
                ObjectVersion.tenant_id == UUID(tenant_id),
            )
        )
        if version is not None:
            version.ingestion_status = IngestionStatus.QUARANTINED.value
            source_object = await session.scalar(
                select(SourceObject).where(SourceObject.id == version.source_object_id)
            )
            # Keep a previously active version searchable while a newer
            # version is being quarantined.  Otherwise a transient ACL
            # failure on the update would hide known-good content.
            if source_object is not None and (
                source_object.current_version_id in {None, version.id}
            ):
                source_object.is_queryable = False
            await session.commit()


async def _complete_run_item(
    tenant_id: str,
    run_id: str | None,
    *,
    failed: bool = False,
    failure_message: str | None = None,
) -> None:
    from worker.tasks.backfill_progress import complete_backfill_item

    await complete_backfill_item(
        tenant_id,
        run_id,
        failed=failed,
        failure_message=failure_message,
    )
