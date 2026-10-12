"""Webhook ingestion task — processes verified webhook deliveries."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.webhook.process_webhook_event")  # type: ignore[misc]
def process_webhook_event(message: dict[str, Any]) -> dict[str, Any]:
    """Process a webhook delivery — fetch, normalise, chunk, embed."""
    from worker.async_runner import run_async

    return run_async(_process_webhook_event(message))


async def _process_webhook_event(message: dict[str, Any]) -> dict[str, Any]:
    from worker.tasks.ingestion_helpers import validate_message

    validate_message(message)
    source = message.get("source", "unknown")
    delivery_id = message.get("deliveryId", "")
    logger.info(
        "Processing webhook event",
        source=source,
        delivery_id=delivery_id,
    )

    # Provider webhooks do not carry a trusted tenant identity.  Reconcile
    # against every connected tenant and let the provider adapter resolve the
    # authoritative object state with that tenant's delegated credential.
    from worker.tasks.reconcile import run_reconciliation

    run_reconciliation.delay(str(source))  # type: ignore[union-attr]

    return {"status": "routed", "source": source, "delivery_id": delivery_id}
