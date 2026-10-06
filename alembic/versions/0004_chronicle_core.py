"""Add Chronicle temporal, replay, audit, and backtest records.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07T00:00:00Z
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.dialects.postgresql.UUID(as_uuid=True)
JSONB = sa.dialects.postgresql.JSONB
TEXT_ARRAY = sa.dialects.postgresql.ARRAY(sa.Text)

_NEW_TENANT_TABLES = (
    "claims",
    "policy_versions",
    "evidence_capsules",
    "audit_checkpoints",
    "query_events",
    "backtest_cases",
    "backtest_runs",
    "backtest_results",
    "fixture_events",
)


def _tenant_column() -> sa.Column:
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False)


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    op.add_column(
        "source_connections",
        sa.Column("mode", sa.Text, nullable=False, server_default="delegated_oauth"),
    )

    for column_name in ("valid_from", "valid_to", "retired_at"):
        op.add_column(
            "identity_links",
            sa.Column(column_name, sa.DateTime(timezone=True)),
        )
    op.add_column(
        "identity_links",
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    for column_name in ("valid_from", "valid_to", "retired_at"):
        op.add_column(
            "acl_grants",
            sa.Column(column_name, sa.DateTime(timezone=True)),
        )

    op.add_column("query_runs", sa.Column("as_of", sa.DateTime(timezone=True)))
    for column_name in (
        "policy_version",
        "embedding_version",
        "context_compiler_version",
        "authorised_context_hash",
    ):
        op.add_column("query_runs", sa.Column(column_name, sa.Text))

    op.add_column(
        "evidence_items",
        sa.Column("object_version_id", UUID, sa.ForeignKey("object_versions.id")),
    )
    op.add_column("evidence_items", sa.Column("content_hash", sa.Text))

    op.create_table(
        "claims",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column(
            "object_version_id",
            UUID,
            sa.ForeignKey("object_versions.id"),
            nullable=False,
        ),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("predicate", sa.Text, nullable=False),
        sa.Column("object_value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1"),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_claims_tenant_subject_predicate",
        "claims",
        ["tenant_id", "subject", "predicate"],
    )
    op.create_index("ix_claims_object_version", "claims", ["object_version_id"])

    op.create_table(
        "policy_versions",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("rules_json", JSONB, nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        _created_at_column(),
        sa.UniqueConstraint("tenant_id", "version"),
    )

    op.create_table(
        "evidence_capsules",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column(
            "query_id",
            UUID,
            sa.ForeignKey("query_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("snapshot_hash", sa.Text, nullable=False),
        sa.Column("context_manifest", JSONB, nullable=False),
        sa.Column("answer_ciphertext", sa.LargeBinary, nullable=False),
        _created_at_column(),
    )
    op.create_index(
        "ix_evidence_capsules_tenant_query",
        "evidence_capsules",
        ["tenant_id", "query_id"],
    )

    op.create_table(
        "audit_checkpoints",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column("chain_head_hash", sa.Text, nullable=False),
        sa.Column("signed_root", sa.Text, nullable=False),
        sa.Column("signer_key_id", sa.Text, nullable=False),
        sa.Column("exported_location", sa.Text, nullable=False),
        _created_at_column(),
    )
    op.create_index(
        "ix_audit_checkpoints_tenant_created",
        "audit_checkpoints",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "query_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column("query_id", UUID, sa.ForeignKey("query_runs.id"), nullable=False),
        sa.Column("sequence_no", sa.Integer, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at_column(),
        sa.UniqueConstraint("query_id", "sequence_no"),
    )
    op.create_index(
        "ix_query_events_tenant_query",
        "query_events",
        ["tenant_id", "query_id", "sequence_no"],
    )

    op.create_table(
        "backtest_cases",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("requester_subject", sa.Text, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True)),
        sa.Column("expected_visibility", JSONB, nullable=False),
        sa.Column("expected_citation_ids", TEXT_ARRAY),
        sa.Column("expected_behavior", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False, server_default="high"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        _created_at_column(),
        sa.UniqueConstraint("tenant_id", "name"),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column("suite_version", sa.Text, nullable=False),
        sa.Column("policy_version", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("embedding_version", sa.Text, nullable=False),
        sa.Column("reranker_version", sa.Text, nullable=False),
        sa.Column("context_compiler_version", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "backtest_results",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column("run_id", UUID, sa.ForeignKey("backtest_runs.id"), nullable=False),
        sa.Column("case_id", UUID, sa.ForeignKey("backtest_cases.id"), nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("answer_hash", sa.Text),
        sa.Column("citation_score", sa.Float),
        sa.Column("leakage_check", sa.Boolean, nullable=False),
        sa.Column("drift_class", sa.Text),
        sa.Column("notes", sa.Text),
        _created_at_column(),
        sa.UniqueConstraint("run_id", "case_id"),
    )
    op.create_index(
        "ix_backtest_results_tenant_run",
        "backtest_results",
        ["tenant_id", "run_id"],
    )

    op.create_table(
        "fixture_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_column(),
        sa.Column("event_key", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        _created_at_column(),
        sa.UniqueConstraint("tenant_id", "event_key"),
    )

    for table in _NEW_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(_NEW_TENANT_TABLES):
        op.drop_table(table)

    op.drop_column("evidence_items", "content_hash")
    op.drop_column("evidence_items", "object_version_id")

    for column_name in (
        "authorised_context_hash",
        "context_compiler_version",
        "embedding_version",
        "policy_version",
        "as_of",
    ):
        op.drop_column("query_runs", column_name)

    for column_name in ("retired_at", "valid_to", "valid_from"):
        op.drop_column("acl_grants", column_name)

    for column_name in ("retired_at", "observed_at", "valid_to", "valid_from"):
        op.drop_column("identity_links", column_name)

    op.drop_column("source_connections", "mode")
