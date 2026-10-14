"""Add explicit folder scoping for Google Drive connections."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store provider-specific connection scope and fail closed old Drive data."""
    op.add_column(
        "source_connections",
        sa.Column(
            "scope_config",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Existing Drive imports were created before a folder scope existed. Do
    # not leave potentially broad personal content queryable after upgrading.
    op.execute(
        "UPDATE source_objects SET is_queryable = false "
        "WHERE source = 'google_drive'"
    )


def downgrade() -> None:
    """Restore the pre-scope schema without re-enabling broad Drive content."""
    op.drop_column("source_connections", "scope_config")
