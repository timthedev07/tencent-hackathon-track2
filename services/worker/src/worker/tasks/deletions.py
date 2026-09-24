"""Deletion handling — priority queue for deletions and ACL changes."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.deletions.process_deletion")
def process_deletion(message: dict[str, Any]) -> dict[str, Any]:
    """Process a deletion or ACL revocation event (highest priority)."""
    import asyncio

    return asyncio.run(_process_deletion(message))


async def _process_deletion(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    logger.info("Processing deletion", source=source)

    # In full implementation:
    # 1. Mark source_objects.is_deleted = True
    # 2. Retire all active object_versions (set retired_at, valid_to)
    # 3. Mark chunks as non-queryable
    # 4. This must happen FAST — deletion takes priority over all ingestion

    return {"status": "deleted", "source": source}
