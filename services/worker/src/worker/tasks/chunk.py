"""Chunking task — split normalised content into 300-800 token chunks."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.domain.types import IngestionStatus
from app.observability.logging import get_logger
from app.persistence.models import Chunk, ObjectVersion
from app.persistence.session import get_session_factory, set_tenant_context
from app.settings import get_settings
from sqlalchemy import delete, select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.chunk.chunk_object")  # type: ignore[misc]
def chunk_object(message: dict[str, Any]) -> dict[str, Any]:
    """Chunk an object and enqueue embedding generation."""
    from worker.async_runner import run_async

    return run_async(_chunk_object(message))


async def _chunk_object(message: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID

    from worker.tasks.ingestion_helpers import validate_message

    validate_message(message)
    tenant_id = _required(message, "tenantId")
    version_id = _required(message, "objectVersionId")
    run_id = _optional_string(message, "backfillRunId")
    source = str(message.get("source", "unknown"))
    logger.info("Chunking object", source=source)
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
            await _complete_run_item(tenant_id, run_id, failed=True)
            return {"status": "missing", "source": source}
        await session.execute(delete(Chunk).where(Chunk.object_version_id == version.id))
        settings = get_settings()
        for ordinal, (section_path, text) in enumerate(
            _chunk_text(version.normalised_content, version.normalised_title)
        ):
            session.add(
                Chunk(
                    tenant_id=UUID(tenant_id),
                    object_version_id=version.id,
                    ordinal=ordinal,
                    section_path=section_path,
                    text=text,
                    embedding_model=settings.EMBEDDING_MODEL,
                    chunk_hash=_chunk_hash(section_path, text),
                    token_count=_token_count(text),
                )
            )
        version.ingestion_status = IngestionStatus.CHUNKED.value
        await session.commit()

    from worker.tasks.embed import generate_embeddings

    generate_embeddings.delay(dict(message))  # type: ignore[union-attr]
    return {"status": "chunked", "source": source}


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


def _chunk_text(content: str, title: str | None) -> list[tuple[list[str], str]]:
    lines = content.splitlines()
    blocks: list[tuple[list[str], str]] = []
    section: list[str] = [title] if title else []
    buffer: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if buffer:
                blocks.extend(_flush(buffer, section))
                buffer = []
            continue
        if _is_heading(line):
            if buffer:
                blocks.extend(_flush(buffer, section))
                buffer = []
            section = ([title] if title else []) + [re.sub(r"^#{1,6}\s*", "", line)]
            continue
        buffer.append(line)
    if buffer:
        blocks.extend(_flush(buffer, section))
    return blocks


def _flush(lines: list[str], section: list[str]) -> list[tuple[list[str], str]]:
    result: list[tuple[list[str], str]] = []
    current: list[str] = []
    current_tokens = 0
    limit = 700
    for line in lines:
        words = line.split()
        for start in range(0, len(words), limit):
            part = words[start : start + limit]
            part_tokens = max(1, (len(" ".join(part)) + 3) // 4)
            if current and current_tokens + part_tokens > limit:
                result.append((section, " ".join(current)))
                current = []
                current_tokens = 0
            current.extend(part)
            current_tokens += part_tokens
    if current:
        result.append((section, " ".join(current)))
    return result


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^#{1,6}\s+\S", line))


def _token_count(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _chunk_hash(section: list[str], text: str) -> str:
    canonical = "\x1f".join(section) + "\x1e" + text
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
