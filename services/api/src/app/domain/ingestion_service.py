"""Ingestion service — webhook delivery handling and ingestion pipeline."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select

from app.observability.logging import get_logger
from app.persistence.models import WebhookDelivery
from app.persistence.session import get_session_factory

logger = get_logger(__name__)


async def handle_webhook_delivery(
    source: str,
    payload: dict[str, Any],
    raw_body: bytes,
) -> None:
    """Handle a verified webhook delivery.

    1. Extract provider delivery/event ID
    2. Deduplicate
    3. Persist safe delivery metadata
    4. Enqueue idempotent task
    """
    delivery_id = _extract_delivery_id(source, payload)

    if delivery_id is None:
        logger.warning("Webhook missing delivery ID", source=source)
        return

    payload_hash = hashlib.sha256(raw_body).hexdigest()

    # Safe metadata — do NOT persist sensitive full payloads
    safe_metadata = _extract_safe_metadata(source, payload)

    factory = get_session_factory()
    async with factory() as session:
        # Deduplicate
        existing = await session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.source == source,
                WebhookDelivery.provider_delivery_id == delivery_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(
                "Duplicate webhook delivery ignored", source=source, delivery_id=delivery_id
            )
            return

        delivery = WebhookDelivery(
            source=source,
            provider_delivery_id=delivery_id,
            signature_valid=True,
            status="received",
            payload_hash=payload_hash,
            safe_metadata=safe_metadata,
        )
        session.add(delivery)
        await session.commit()

    # Enqueue idempotent ingestion task
    from worker.tasks.webhook import process_webhook_event

    process_webhook_event.delay(
        {
            "schemaVersion": 1,
            "source": source,
            "deliveryId": delivery_id,
            "safeMetadata": safe_metadata,
        }
    )
    logger.info("Webhook enqueued for processing", source=source, delivery_id=delivery_id)


def _extract_delivery_id(source: str, payload: dict[str, Any]) -> str | None:
    """Extract the provider delivery/event ID from the payload."""
    if source == "slack":
        return payload.get("event_id") or payload.get("api_app_id")
    if source in ("confluence", "jira"):
        return payload.get("event_id") or payload.get("webhook_id")
    if source == "google_drive":
        return payload.get("uuid") or payload.get("id")
    return None


def _extract_safe_metadata(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Extract only safe metadata — no sensitive content."""
    safe: dict[str, Any] = {"source": source}
    if "event" in payload:
        event = payload["event"]
        if isinstance(event, dict):
            safe["event_type"] = event.get("type")
            safe["channel"] = event.get("channel") if source == "slack" else None
    if "change_type" in payload:
        safe["change_type"] = payload["change_type"]
    return {k: v for k, v in safe.items() if v is not None}
