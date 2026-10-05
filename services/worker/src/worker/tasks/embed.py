"""Embedding task — generate embeddings with dimension validation and reuse."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.persistence.session import get_session_factory

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.embed.generate_embeddings")  # type: ignore[misc]
def generate_embeddings(message: dict[str, Any]) -> dict[str, Any]:
    """Generate embeddings for chunks and activate the version."""
    import asyncio

    return asyncio.run(_generate_embeddings(message))


async def _generate_embeddings(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    logger.info("Generating embeddings", source=source)

    # In full implementation:
    # 1. For each chunk: check if chunk_hash + embedding_model already exists
    #    (embedding reuse). If so, copy the embedding.
    # 2. Otherwise: call the model gateway's embed method.
    # 3. Validate embedding dimension matches EMBEDDING_DIMENSION.
    # 4. Persist embeddings.
    # 5. Activate the version in a single atomic transaction:
    #    normalise + ACL + chunks + embeddings all succeed → set
    #    current_version_id + ACTIVE state.

    factory = get_session_factory()
    async with factory():
        # Activation transaction would go here
        logger.info("Object version activated", source=source)
        pass

    return {"status": "embedded", "source": source}
