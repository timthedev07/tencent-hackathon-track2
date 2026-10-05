"""Chunking task — split normalised content into 300-800 token chunks."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.chunk.chunk_object")  # type: ignore[misc]
def chunk_object(message: dict[str, Any]) -> dict[str, Any]:
    """Chunk an object and enqueue embedding generation."""
    import asyncio

    return asyncio.run(_chunk_object(message))


async def _chunk_object(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    logger.info("Chunking object", source=source)

    # In full implementation: use tiktoken to count tokens, create chunks
    # at structural boundaries, compute chunk_hash = SHA-256(canonical text +
    # structural metadata), persist chunks to PostgreSQL.

    from worker.tasks.embed import generate_embeddings

    generate_embeddings.delay(message)  # type: ignore[union-attr]
    return {"status": "chunked", "source": source}
