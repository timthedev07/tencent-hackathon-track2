"""Run async ingestion code on one event loop per Celery child process."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_runner: asyncio.Runner | None = None


def run_async(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Keep asyncpg and SQLAlchemy connections on the loop that owns them."""
    global _runner
    if _runner is None:
        _runner = asyncio.Runner()
    return _runner.run(coroutine)
