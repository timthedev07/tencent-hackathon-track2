"""Make one cursor authoritative per tenant connection and cursor type."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_ingestion_cursors_tenant_connection_type",
        "ingestion_cursors",
        ["tenant_id", "source_connection_id", "cursor_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ingestion_cursors_tenant_connection_type",
        "ingestion_cursors",
        type_="unique",
    )
