"""Remove source connection mode from databases that already ran 0005."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the obsolete fixture/live discriminator without touching data."""
    op.execute("ALTER TABLE source_connections DROP COLUMN IF EXISTS mode")


def downgrade() -> None:
    """Do not restore the removed fixture mode on downgrade."""
