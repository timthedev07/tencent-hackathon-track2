"""Reconciliation scheduler — runs at 15-30 minute intervals."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.reconcile.run_reconciliation")
def run_reconciliation() -> dict[str, Any]:
    """Run reconciliation for all source connections."""
    import asyncio

    return asyncio.run(_run_reconciliation())


async def _run_reconciliation() -> dict[str, Any]:
    logger.info("Running reconciliation")
    # In full implementation: iterate all source connections,
    # call read_changes on each connector, detect drift,
    # enqueue fetch/normalise for any changed objects,
    # enqueue deletions for any removed objects.
    return {"status": "reconciled"}
