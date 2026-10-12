"""Source fetch task — fetches object content from provider."""

from __future__ import annotations

import hashlib
from typing import Any

from app.connectors.errors import ConnectorError, NotFoundError, RateLimitedError, TransientError
from app.domain.types import ConnectorOutcome, IngestionStatus
from app.observability.logging import get_logger
from app.persistence.models import ObjectVersion, SourceObject
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[misc]
    name="worker.tasks.fetch.fetch_source_object",
    autoretry_for=(RateLimitedError, TransientError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def fetch_source_object(message: dict[str, Any]) -> dict[str, Any]:
    """Fetch a source object and enqueue normalisation."""
    from worker.async_runner import run_async

    return run_async(_fetch_source_object(message))


async def _fetch_source_object(message: dict[str, Any]) -> dict[str, Any]:
    from worker.tasks.ingestion_helpers import (
        connector_for,
        load_ingestion_context,
        validate_message,
    )

    validate_message(message)
    tenant_id = _required(message, "tenantId")
    connection_id = _required(message, "connectionId")
    external_id = _required(message, "externalObjectId")
    user_id = _optional_string(message, "userId")
    run_id = _optional_string(message, "backfillRunId")
    context = await load_ingestion_context(tenant_id, connection_id, user_id)
    source = context.connection.source
    logger.info("Fetching source object", source=source.value)
    try:
        result = await connector_for(source).fetch_object(
            context.connection,
            external_id,
            context.credential,
        )
    except NotFoundError:
        await _mark_deleted(tenant_id, connection_id, source.value, external_id)
        await _complete_run_item(tenant_id, run_id)
        return {"status": "not_found", "source": source.value}
    except ConnectorError as exc:
        if exc.outcome in {
            ConnectorOutcome.FORBIDDEN,
            ConnectorOutcome.NOT_FOUND,
            ConnectorOutcome.PERMANENT_ERROR,
        }:
            await _mark_not_queryable(tenant_id, connection_id, source.value, external_id)
            await _complete_run_item(tenant_id, run_id, failed=True)
            return {"status": "not_queryable", "source": source.value}
        raise

    if result.outcome != ConnectorOutcome.SUCCESS:
        if result.outcome == ConnectorOutcome.NOT_FOUND:
            await _mark_deleted(tenant_id, connection_id, source.value, external_id)
            await _complete_run_item(tenant_id, run_id)
            return {"status": "not_found", "source": source.value}
        raise ValueError(f"SOURCE_FETCH_{result.outcome.value.upper()}")

    source_object_id, version_id, duplicate = await _persist_fetched(
        tenant_id,
        connection_id,
        source.value,
        result,
    )
    if duplicate:
        await _complete_run_item(tenant_id, run_id)
        return {"status": "duplicate", "source": source.value}
    next_message = {
        "tenantId": tenant_id,
        "connectionId": connection_id,
        "sourceObjectId": str(source_object_id),
        "objectVersionId": str(version_id),
        "externalObjectId": external_id,
        "sourceRevision": result.source_revision,
        "source": source.value,
    }
    if user_id:
        next_message["userId"] = user_id
    if run_id:
        next_message["backfillRunId"] = run_id
    from worker.tasks.normalise import normalise_object

    normalise_object.delay(next_message)  # type: ignore[union-attr]
    return {"status": "fetched", "source": source.value, "versionId": str(version_id)}


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


async def _complete_run_item(tenant_id: str, run_id: str | None, *, failed: bool = False) -> None:
    from worker.tasks.backfill_progress import complete_backfill_item

    await complete_backfill_item(tenant_id, run_id, failed=failed)


async def _persist_fetched(
    tenant_id: str,
    connection_id: str,
    source: str,
    result: Any,
) -> tuple[str, str, bool]:
    from uuid import UUID

    now_hash = hashlib.sha256((result.content or "").encode("utf-8")).hexdigest()
    revision = str(result.source_revision or now_hash)
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, UUID(tenant_id))
        source_object = await session.scalar(
            select(SourceObject).where(
                SourceObject.tenant_id == UUID(tenant_id),
                SourceObject.source_connection_id == UUID(connection_id),
                SourceObject.external_object_id == result.external_object_id,
            )
        )
        if source_object is None:
            source_object = SourceObject(
                tenant_id=UUID(tenant_id),
                source_connection_id=UUID(connection_id),
                source=source,
                external_object_id=result.external_object_id,
                object_type=result.object_type or "unknown",
            )
            session.add(source_object)
            await session.flush()
        else:
            source_object.object_type = result.object_type or source_object.object_type
        source_object.parent_external_id = result.parent_external_id
        source_object.canonical_url = result.canonical_url
        source_object.is_deleted = False
        existing = await session.scalar(
            select(ObjectVersion).where(
                ObjectVersion.source_object_id == source_object.id,
                ObjectVersion.source_revision == revision,
            )
        )
        if existing is not None:
            if existing.valid_from is None and result.last_modified_at is not None:
                existing.valid_from = result.last_modified_at
            await session.commit()
            return str(source_object.id), str(existing.id), True
        version = ObjectVersion(
            tenant_id=UUID(tenant_id),
            source_object_id=source_object.id,
            source_revision=revision,
            normalised_content=result.content or "",
            normalised_title=result.title,
            content_hash=now_hash,
            valid_from=result.last_modified_at,
            ingestion_status=IngestionStatus.FETCHED.value,
        )
        session.add(version)
        await session.flush()
        await session.commit()
        return str(source_object.id), str(version.id), False


async def _mark_deleted(tenant_id: str, connection_id: str, source: str, external_id: str) -> None:
    await _set_object_state(tenant_id, connection_id, source, external_id, deleted=True)


async def _mark_not_queryable(
    tenant_id: str,
    connection_id: str,
    source: str,
    external_id: str,
) -> None:
    await _set_object_state(tenant_id, connection_id, source, external_id, deleted=False)


async def _set_object_state(
    tenant_id: str,
    connection_id: str,
    source: str,
    external_id: str,
    *,
    deleted: bool,
) -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, UUID(tenant_id))
        row = await session.scalar(
            select(SourceObject).where(
                SourceObject.tenant_id == UUID(tenant_id),
                SourceObject.source_connection_id == UUID(connection_id),
                SourceObject.source == source,
                SourceObject.external_object_id == external_id,
            )
        )
        if row is not None:
            row.is_deleted = deleted
            row.is_queryable = False
            if deleted:
                now = datetime.now(UTC)
                row.current_version_id = None
                versions = (
                    await session.scalars(
                        select(ObjectVersion).where(ObjectVersion.source_object_id == row.id)
                    )
                ).all()
                for version in versions:
                    version.retired_at = version.retired_at or now
                    version.valid_to = version.valid_to or now
            await session.commit()
