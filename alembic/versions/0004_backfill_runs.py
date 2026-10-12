"""Persist manual import progress for the Settings experience.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12T00:00:00Z
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backfill_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "source_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_connections.id"),
            nullable=False,
        ),
        sa.Column("requested_by_user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("discovered_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enqueued_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_backfill_runs_tenant_connection_created",
        "backfill_runs",
        ["tenant_id", "source_connection_id", "created_at"],
    )
    op.create_index(
        "ix_backfill_runs_tenant_requester_created",
        "backfill_runs",
        ["tenant_id", "requested_by_user_id", "created_at"],
    )
    op.execute("ALTER TABLE backfill_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE backfill_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_backfill_runs ON backfill_runs "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_table("backfill_runs")
