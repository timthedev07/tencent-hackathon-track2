"""Record the exact embedding implementation used by each query."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("query_runs", sa.Column("embedding_version", sa.Text))


def downgrade() -> None:
    op.drop_column("query_runs", "embedding_version")
