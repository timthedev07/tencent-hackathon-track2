"""Deletion handling — priority queue for deletions and ACL changes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.observability.logging import get_logger
from app.persistence.models import ObjectVersion, SourceObject
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.deletions.process_deletion")  # type: ignore[misc]
def process_deletion(message: dict[str, Any]) -> dict[str, Any]:
    """Process a deletion or ACL revocation event (highest priority)."""
    import asyncio

    return asyncio.run(_process_deletion(message))


async def _process_deletion(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    tenant_text = message.get("tenantId") or message.get("tenant_id")
    object_text = message.get("sourceObjectId") or message.get("source_object_id")
    external_id = message.get("externalObjectId") or message.get("external_object_id")
    if not tenant_text or not (object_text or external_id):
        raise ValueError("SOURCE_OBJECT_NOT_FOUND")

    tenant_id = UUID(str(tenant_text))
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        stmt = select(SourceObject).where(SourceObject.tenant_id == tenant_id)
        if object_text:
            stmt = stmt.where(SourceObject.id == UUID(str(object_text)))
        else:
            stmt = stmt.where(
                SourceObject.source == str(source),
                SourceObject.external_object_id == str(external_id),
            )
        source_object = (await session.execute(stmt)).scalar_one_or_none()
        if source_object is None:
            return {"status": "not_found", "source": source}

        now = datetime.now(UTC)
        source_object.is_deleted = True
        source_object.is_queryable = False
        source_object.current_version_id = None
        for version in (
            await session.execute(
                select(ObjectVersion).where(ObjectVersion.source_object_id == source_object.id)
            )
        ).scalars():
            version.retired_at = version.retired_at or now
            version.valid_to = version.valid_to or now
        await session.commit()

    logger.info("Source object deleted", source=source)
    return {"status": "deleted", "source": source}
