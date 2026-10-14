"""Remove the obsolete fixture connection mode column."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the mode column when upgrading an older local database."""
    op.execute("ALTER TABLE source_connections DROP COLUMN IF EXISTS mode")


def downgrade() -> None:
    """The fixture mode is intentionally not restored on downgrade."""
