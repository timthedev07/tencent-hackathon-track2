"""RLS policy helpers and middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass

# All tenant-scoped tables that require RLS
TENANT_SCOPED_TABLES = [
    "users",
    "source_connections",
    "encrypted_credentials",
    "identity_links",
    "source_objects",
    "object_versions",
    "chunks",
    "principals",
    "acl_grants",
    "ingestion_cursors",
    "webhook_deliveries",
    "query_runs",
    "access_decisions",
    "evidence_items",
    "audit_events",
    "dead_letter_tasks",
]


async def enable_rls_on_all_tables(session: AsyncSession) -> None:
    """Enable RLS on all tenant-scoped tables.

    Used in migrations to set up RLS policies.
    """
    for table in TENANT_SCOPED_TABLES:
        await session.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        await session.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        await session.execute(
            text(
                f"CREATE POLICY IF NOT EXISTS tenant_isolation_{table} "
                f"ON {table} "
                f"USING (tenant_id = current_setting('app.tenant_id')::uuid)"
            )
        )
