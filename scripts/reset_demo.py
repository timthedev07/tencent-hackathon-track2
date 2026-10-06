#!/usr/bin/env python3
"""Reset demo environment to clean state."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api" / "src"))


async def main() -> None:
    """Truncate all data tables and re-seed fixtures for a clean demo."""
    from app.persistence.session import get_engine
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        # Disable RLS for truncation
        await conn.execute(text("SET ROLE assistant"))
        # Truncate all tables in dependency order
        tables = [
            "fixture_events",
            "backtest_results",
            "backtest_runs",
            "backtest_cases",
            "query_events",
            "audit_checkpoints",
            "evidence_capsules",
            "policy_versions",
            "claims",
            "audit_events",
            "evidence_items",
            "access_decisions",
            "query_runs",
            "chunks",
            "object_versions",
            "acl_grants",
            "principals",
            "source_objects",
            "ingestion_cursors",
            "webhook_deliveries",
            "identity_links",
            "encrypted_credentials",
            "source_connections",
            "users",
            "tenants",
        ]
        for table in tables:
            await conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))

    await engine.dispose()

    from seed_fixtures import main as seed_fixtures

    await seed_fixtures()


if __name__ == "__main__":
    asyncio.run(main())
