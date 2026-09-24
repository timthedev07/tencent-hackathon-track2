"""Webhook ingestion task — processes verified webhook deliveries."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.webhook.process_webhook_event")
def process_webhook_event(message: dict[str, Any]) -> dict[str, Any]:
    """Process a webhook delivery — fetch, normalise, chunk, embed."""
    import asyncio

    return asyncio.run(_process_webhook_event(message))


async def _process_webhook_event(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source", "unknown")
    delivery_id = message.get("deliveryId", "")
    safe_metadata = message.get("safeMetadata", {})

    logger.info(
        "Processing webhook event",
        source=source,
        delivery_id=delivery_id,
    )

    # Determine event type and route to appropriate task
    event_type = safe_metadata.get("event_type") or safe_metadata.get("change_type")

    if event_type in ("deleted", "removed"):
        # Priority: deletions queue
        from worker.tasks.deletions import process_deletion

        process_deletion.delay(message)
    else:
        # Standard ingestion: fetch → normalise → ACL → chunk → embed
        from worker.tasks.fetch import fetch_source_object

        fetch_source_object.delay(message)

    return {"status": "routed", "source": source, "delivery_id": delivery_id}
