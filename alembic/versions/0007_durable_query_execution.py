"""Add replayable query events and worker execution leases."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "query_runs",
        sa.Column("execution_attempt", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("query_runs", sa.Column("execution_lease_token", sa.Text))
    op.add_column(
        "query_runs",
        sa.Column("execution_lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "query_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("query_id", UUID(as_uuid=True), sa.ForeignKey("query_runs.id"), nullable=False),
        sa.Column("sequence_no", sa.Integer, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("query_id", "sequence_no"),
    )
    op.create_index(
        "ix_query_events_tenant_query",
        "query_events",
        ["tenant_id", "query_id", "sequence_no"],
    )
    op.execute("ALTER TABLE query_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE query_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_query_events ON query_events "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_index("ix_query_events_tenant_query", table_name="query_events")
    op.drop_table("query_events")
    op.drop_column("query_runs", "execution_lease_expires_at")
    op.drop_column("query_runs", "execution_lease_token")
    op.drop_column("query_runs", "execution_attempt")
