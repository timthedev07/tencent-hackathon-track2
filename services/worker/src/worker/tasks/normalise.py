"""Normalisation task — raw provider data → NormalisedObject."""

from __future__ import annotations

import re
from typing import Any

from app.connectors.parsing import strip_html
from app.domain.types import IngestionStatus
from app.observability.logging import get_logger
from app.persistence.models import ObjectVersion
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.normalise.normalise_object")  # type: ignore[misc]
def normalise_object(message: dict[str, Any]) -> dict[str, Any]:
    """Normalise a fetched object and enqueue ACL resolution."""
    from worker.async_runner import run_async

    return run_async(_normalise_object(message))


async def _normalise_object(message: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID

    from worker.tasks.ingestion_helpers import validate_message

    validate_message(message)
    tenant_id = _required(message, "tenantId")
    version_id = _required(message, "objectVersionId")
    run_id = _optional_string(message, "backfillRunId")
    source = str(message.get("source", "unknown"))
    logger.info("Normalising object", source=source)
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
        content = _normalise_content(version.normalised_content, version.normalised_title)
        version.normalised_content = content
        if not content:
            version.ingestion_status = IngestionStatus.QUARANTINED.value
            await session.commit()
            await _complete_run_item(
                tenant_id, run_id, failed=True, failure_message="SOURCE_CONTENT_EMPTY"
            )
            return {"status": "quarantined", "source": source}
        version.ingestion_status = IngestionStatus.NORMALISED.value
        await session.commit()

    next_message = dict(message)
    from worker.tasks.acl import resolve_acl

    resolve_acl.delay(next_message)  # type: ignore[union-attr]
    return {"status": "normalised", "source": source}


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


def _normalise_text(value: str | None) -> str:
    if not value:
        return ""
    without_controls = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    return strip_html(without_controls)


def _normalise_content(content: str | None, title: str | None) -> str:
    """Keep title-only provider objects searchable instead of quarantining them."""
    return _normalise_text(content) or _normalise_text(title)
