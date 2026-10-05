"""Normalisation task — raw provider data → NormalisedObject."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.normalise.normalise_object")  # type: ignore[misc]
def normalise_object(message: dict[str, Any]) -> dict[str, Any]:
    """Normalise a fetched object and enqueue ACL resolution."""
    import asyncio

    return asyncio.run(_normalise_object(message))


async def _normalise_object(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    logger.info("Normalising object", source=source)

    # In full implementation: call the connector's normalise method,
    # persist the NormalisedObject, then enqueue ACL resolution.
    from worker.tasks.acl import resolve_acl

    resolve_acl.delay(message)  # type: ignore[union-attr]
    return {"status": "normalised", "source": source}
