"""Source fetch task — fetches object content from provider."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.fetch.fetch_source_object")  # type: ignore[misc]
def fetch_source_object(message: dict[str, Any]) -> dict[str, Any]:
    """Fetch a source object and enqueue normalisation."""
    import asyncio

    return asyncio.run(_fetch_source_object(message))


async def _fetch_source_object(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    logger.info("Fetching source object", source=source)

    # In full implementation: call the connector's fetch_object method,
    # persist the raw object data, then enqueue normalisation.
    from worker.tasks.normalise import normalise_object

    normalise_object.delay(message)  # type: ignore[union-attr]
    return {"status": "fetched", "source": source}
