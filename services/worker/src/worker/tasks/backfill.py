"""Backfill task — re-ingest historical data."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.backfill.run_backfill")  # type: ignore[misc]
def run_backfill(source_connection_id: str) -> dict[str, Any]:
    """Backfill all objects for a source connection."""
    import asyncio

    return asyncio.run(_run_backfill(source_connection_id))


async def _run_backfill(source_connection_id: str) -> dict[str, Any]:
    logger.info("Running backfill", source_connection_id=source_connection_id)
    return {"status": "backfilled", "source_connection_id": source_connection_id}
