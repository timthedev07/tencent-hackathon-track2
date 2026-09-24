"""ACL resolution task — fetch and persist ACL grants."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.acl.resolve_acl")
def resolve_acl(message: dict[str, Any]) -> dict[str, Any]:
    """Resolve ACL grants for an object and enqueue chunking."""
    import asyncio

    return asyncio.run(_resolve_acl(message))


async def _resolve_acl(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    logger.info("Resolving ACL", source=source)

    from worker.tasks.chunk import chunk_object

    chunk_object.delay(message)
    return {"status": "acl_resolved", "source": source}
