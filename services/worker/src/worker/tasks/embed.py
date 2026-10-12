"""Embedding task — generate embeddings with dimension validation and reuse."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.types import IngestionStatus, Source
from app.models.gateway import get_model_gateway
from app.observability.logging import get_logger
from app.persistence.models import Chunk, ObjectVersion, SourceObject
from app.persistence.session import get_session_factory, set_tenant_context
from app.settings import get_settings
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.embed.generate_embeddings")  # type: ignore[misc]
def generate_embeddings(message: dict[str, Any]) -> dict[str, Any]:
    """Generate embeddings for chunks and activate the version."""
    from worker.async_runner import run_async

    return run_async(_generate_embeddings(message))


async def _generate_embeddings(message: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID

    from worker.tasks.ingestion_helpers import validate_message

    validate_message(message)
    tenant_id = _required(message, "tenantId")
    version_id = _required(message, "objectVersionId")
    run_id = _optional_string(message, "backfillRunId")
    source = str(message.get("source", "unknown"))
    logger.info("Generating embeddings", source=source)
    factory = get_session_factory()
    settings = get_settings()
    async with factory() as session:
        await set_tenant_context(session, UUID(tenant_id))
        version = await session.scalar(
            select(ObjectVersion).where(
                ObjectVersion.id == UUID(version_id),
                ObjectVersion.tenant_id == UUID(tenant_id),
            )
        )
        if version is None:
            await _complete_run_item(tenant_id, run_id, failed=True)
            return {"status": "missing", "source": source}
        chunks = list(
            (
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.object_version_id == version.id,
                        Chunk.tenant_id == UUID(tenant_id),
                    )
                    .order_by(Chunk.ordinal)
                )
            ).all()
        )
        hashes = [chunk.chunk_hash for chunk in chunks]
        reusable = {}
        if hashes:
            reusable_rows = (
                await session.scalars(
                    select(Chunk).where(
                        Chunk.tenant_id == UUID(tenant_id),
                        Chunk.embedding_model == settings.EMBEDDING_MODEL,
                        Chunk.chunk_hash.in_(hashes),
                        Chunk.embedding.is_not(None),
                    )
                )
            ).all()
            reusable = {row.chunk_hash: row.embedding for row in reusable_rows if row.embedding}

    missing = [chunk for chunk in chunks if chunk.chunk_hash not in reusable]
    generated: list[list[float]] = []
    if missing:
        try:
            generated = await get_model_gateway().embed(
                [chunk.text for chunk in missing], settings.EMBEDDING_MODEL
            )
        except Exception:
            await _quarantine(tenant_id, version_id)
            await _complete_run_item(tenant_id, run_id, failed=True)
            raise
        if len(generated) != len(missing) or any(
            len(vector) != settings.EMBEDDING_DIMENSION for vector in generated
        ):
            await _quarantine(tenant_id, version_id)
            await _complete_run_item(tenant_id, run_id, failed=True)
            raise ValueError("EMBEDDING_DIMENSION_MISMATCH")
    embeddings = dict(reusable)
    embeddings.update(
        {chunk.chunk_hash: vector for chunk, vector in zip(missing, generated, strict=True)}
    )

    now = datetime.now(UTC)
    async with factory() as session:
        await set_tenant_context(session, UUID(tenant_id))
        version = await session.scalar(
            select(ObjectVersion).where(
                ObjectVersion.id == UUID(version_id),
                ObjectVersion.tenant_id == UUID(tenant_id),
            )
        )
        if version is None:
            await _complete_run_item(tenant_id, run_id, failed=True)
            return {"status": "missing", "source": source}
        chunks = list(
            (
                await session.scalars(
                    select(Chunk)
                    .where(Chunk.object_version_id == version.id)
                    .order_by(Chunk.ordinal)
                )
            ).all()
        )
        for chunk in chunks:
            chunk.embedding = embeddings.get(chunk.chunk_hash)
        source_object = await session.scalar(
            select(SourceObject).where(SourceObject.id == version.source_object_id)
        )
        if source_object is None or not chunks or any(chunk.embedding is None for chunk in chunks):
            version.ingestion_status = IngestionStatus.QUARANTINED.value
            await session.commit()
            await _complete_run_item(tenant_id, run_id, failed=True)
            return {"status": "quarantined", "source": source}
        if source_object.current_version_id and source_object.current_version_id != version.id:
            previous = await session.scalar(
                select(ObjectVersion).where(ObjectVersion.id == source_object.current_version_id)
            )
            if previous is not None and _is_older_revision(
                Source(source), version.source_revision, previous.source_revision
            ):
                version.valid_to = now
                version.retired_at = now
                await session.commit()
                await _complete_run_item(tenant_id, run_id)
                return {"status": "obsolete", "source": source}
        else:
            previous = None
        if previous is not None:
            previous.valid_to = now
            previous.retired_at = now
        version.valid_from = version.valid_from or now
        version.ingestion_status = IngestionStatus.ACTIVE.value
        source_object.current_version_id = version.id
        source_object.current_revision = version.source_revision
        source_object.is_deleted = False
        source_object.is_queryable = True
        await session.commit()
        logger.info("Object version activated", source=source)

    await _complete_run_item(tenant_id, run_id)
    return {"status": "embedded", "source": source}


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
            await session.commit()


def _is_older_revision(source: Source, candidate: str, current: str) -> bool:
    """Compare only provider revisions with a documented total ordering."""
    if source in {Source.CONFLUENCE, Source.SLACK}:
        try:
            return float(candidate) < float(current)
        except ValueError:
            return False
    if source in {Source.JIRA, Source.GOOGLE_DRIVE}:
        try:
            candidate_time = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            current_time = datetime.fromisoformat(current.replace("Z", "+00:00"))
        except ValueError:
            try:
                return int(candidate) < int(current)
            except ValueError:
                return False
        else:
            return candidate_time < current_time
    return False
