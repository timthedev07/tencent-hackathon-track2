"""Backtest tasks — retrieval quality evaluation."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.backtests.run_retrieval_backtest")  # type: ignore[misc]
def run_retrieval_backtest(query_set_id: str) -> dict[str, Any]:
    """Run retrieval quality backtests against a golden query set."""
    import asyncio

    return asyncio.run(_run_retrieval_backtest(query_set_id))


async def _run_retrieval_backtest(query_set_id: str) -> dict[str, Any]:
    logger.info("Running retrieval backtest", query_set_id=query_set_id)
    # In full implementation: load golden query set, run queries,
    # measure recall@50, recall@30, recall@10, MRR, citation precision.
    return {"status": "completed", "query_set_id": query_set_id}
