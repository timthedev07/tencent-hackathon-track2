"""Allow tenant-neutral, verified provider webhook ingress metadata."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY tenant_isolation_webhook_deliveries ON webhook_deliveries")
    op.execute(
        "CREATE POLICY tenant_isolation_webhook_deliveries "
        "ON webhook_deliveries "
        "USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation_webhook_deliveries ON webhook_deliveries")
    op.execute(
        "CREATE POLICY tenant_isolation_webhook_deliveries "
        "ON webhook_deliveries "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
