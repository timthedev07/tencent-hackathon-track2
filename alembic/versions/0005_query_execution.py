"""Add durable query execution lease fields.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08T00:00:00Z
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
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


def downgrade() -> None:
    op.drop_column("query_runs", "execution_lease_expires_at")
    op.drop_column("query_runs", "execution_lease_token")
    op.drop_column("query_runs", "execution_attempt")
