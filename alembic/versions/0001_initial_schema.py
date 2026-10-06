"""Initial schema — all 17 tables + RLS + indexes

Revision ID: 0001
Revises:
Create Date: 2026-09-24T00:00:00Z
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from app.settings import get_settings
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── pgvector extension ───────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── tenants ──────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_org_key", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("external_org_key"),
    )

    # ── users ────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("oidc_subject", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text),
        sa.Column("email", sa.Text),
        sa.Column(
            "is_compliance_user", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "oidc_subject"),
    )

    # ── source_connections ────────────────────────────────────────
    op.create_table(
        "source_connections",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("external_workspace_id", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="connected"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_reconcile_at", sa.DateTime(timezone=True)),
    )

    # ── encrypted_credentials ─────────────────────────────────────
    op.create_table(
        "encrypted_credentials",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column(
            "source_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_connections.id"),
            nullable=False,
        ),
        sa.Column("credential_type", sa.Text, nullable=False),
        sa.Column("ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("key_version", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("scope", sa.dialects.postgresql.ARRAY(sa.Text)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # ── identity_links ───────────────────────────────────────────
    op.create_table(
        "identity_links",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "source_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_connections.id"),
            nullable=False,
        ),
        sa.Column("provider_subject_id", sa.Text, nullable=False),
        sa.Column("provider_account_type", sa.Text),
        sa.Column(
            "verified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("verified_by", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="verified"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("source_connection_id", "provider_subject_id"),
        sa.UniqueConstraint("user_id", "source_connection_id"),
    )

    # ── source_objects ───────────────────────────────────────────
    op.create_table(
        "source_objects",
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
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("external_object_id", sa.Text, nullable=False),
        sa.Column("object_type", sa.Text, nullable=False),
        sa.Column("parent_external_id", sa.Text),
        sa.Column("canonical_url", sa.Text),
        sa.Column("current_revision", sa.Text),
        sa.Column("current_version_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_queryable", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "source", "external_object_id"),
    )
    op.create_index(
        "ix_source_objects_tenant_source_ext",
        "source_objects",
        ["tenant_id", "source", "external_object_id"],
    )
    op.create_index(
        "ix_source_objects_tenant_queryable", "source_objects", ["tenant_id", "is_queryable"]
    )
    op.create_index(
        "ix_source_objects_tenant_conn", "source_objects", ["tenant_id", "source_connection_id"]
    )

    # ── object_versions ──────────────────────────────────────────
    op.create_table(
        "object_versions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "source_object_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_objects.id"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Text, nullable=False),
        sa.Column("normalised_title", sa.Text),
        sa.Column("normalised_content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("ingestion_status", sa.Text, nullable=False, server_default="received"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("source_object_id", "source_revision"),
    )
    op.create_index(
        "ix_object_versions_obj_rev", "object_versions", ["source_object_id", "source_revision"]
    )
    op.create_index(
        "ix_object_versions_tenant_observed",
        "object_versions",
        ["tenant_id", sa.text("observed_at DESC")],
    )
    op.create_index(
        "ix_object_versions_tenant_status", "object_versions", ["tenant_id", "ingestion_status"]
    )

    # ── chunks ───────────────────────────────────────────────────
    dim = get_settings().EMBEDDING_DIMENSION
    op.create_table(
        "chunks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "object_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("object_versions.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("section_path", sa.dialects.postgresql.ARRAY(sa.Text)),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("tsv", sa.dialects.postgresql.TSVECTOR),
        sa.Column("embedding", Vector(dim)),
        sa.Column("embedding_model", sa.Text, nullable=False),
        sa.Column("chunk_hash", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("object_version_id", "ordinal"),
    )
    op.create_index("ix_chunks_tenant_version", "chunks", ["tenant_id", "object_version_id"])
    op.create_index("ix_chunks_tsv", "chunks", [sa.text("tsv")], postgresql_using="gin")
    # HNSW index for vector search
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    # Full-text search trigger
    op.execute(
        "CREATE TRIGGER chunks_tsv_trigger BEFORE INSERT OR UPDATE ON chunks "
        "FOR EACH ROW EXECUTE FUNCTION "
        "tsvector_update_trigger(tsv, 'pg_catalog.english', text)"
    )

    # ── principals ────────────────────────────────────────────────
    op.create_table(
        "principals",
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
        sa.Column("principal_type", sa.Text, nullable=False),
        sa.Column("provider_principal_id", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # ── acl_grants ────────────────────────────────────────────────
    op.create_table(
        "acl_grants",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "source_object_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_objects.id"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id"),
            nullable=False,
        ),
        sa.Column("permission", sa.Text, nullable=False),
        sa.Column("grant_effect", sa.Text, nullable=False),
        sa.Column("inheritance_path", sa.dialects.postgresql.JSONB),
        sa.Column("source_revision", sa.Text),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_acl_grants_tenant_obj", "acl_grants", ["tenant_id", "source_object_id"])
    op.create_index("ix_acl_grants_tenant_principal", "acl_grants", ["tenant_id", "principal_id"])
    op.create_index("ix_acl_grants_obj_perm", "acl_grants", ["source_object_id", "permission"])

    # ── ingestion_cursors ─────────────────────────────────────────
    op.create_table(
        "ingestion_cursors",
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
        sa.Column("cursor_type", sa.Text, nullable=False),
        sa.Column("cursor_value", sa.Text),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # ── webhook_deliveries ────────────────────────────────────────
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id")
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("provider_delivery_id", sa.Text, nullable=False),
        sa.Column("signature_valid", sa.Boolean, nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="received"),
        sa.Column("payload_hash", sa.Text),
        sa.Column("safe_metadata", sa.dialects.postgresql.JSONB),
        sa.UniqueConstraint("source", "provider_delivery_id"),
    )

    # ── query_runs ───────────────────────────────────────────────
    op.create_table(
        "query_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("query_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="created"),
        sa.Column("partial_sources", sa.dialects.postgresql.ARRAY(sa.Text)),
        sa.Column("prompt_version", sa.Text),
        sa.Column("model_version", sa.Text),
        sa.Column("retriever_version", sa.Text),
        sa.Column("reranker_version", sa.Text),
        sa.Column("context_policy_version", sa.Text),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("error_code", sa.Text),
        sa.Column("answer_ciphertext", sa.LargeBinary),
    )
    op.create_index(
        "ix_query_runs_tenant_user_started",
        "query_runs",
        ["tenant_id", "user_id", sa.text("started_at DESC")],
    )
    op.create_index("ix_query_runs_tenant_status", "query_runs", ["tenant_id", "status"])

    # ── access_decisions ──────────────────────────────────────────
    op.create_table(
        "access_decisions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "query_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("query_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "source_object_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_objects.id"),
            nullable=False,
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("reason_code", sa.Text),
        sa.Column(
            "access_checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("provider_latency_ms", sa.Integer),
        sa.Column("provider_request_id", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_access_decisions_query", "access_decisions", ["query_id"])
    op.create_index(
        "ix_access_decisions_tenant_obj", "access_decisions", ["tenant_id", "source_object_id"]
    )

    # ── evidence_items ────────────────────────────────────────────
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "query_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("query_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id"),
            nullable=False,
        ),
        sa.Column(
            "source_object_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_objects.id"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("title_snapshot", sa.Text),
        sa.Column("canonical_url", sa.Text),
        sa.Column("quoted_span_ciphertext", sa.LargeBinary),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("retrieval_score", sa.Float),
        sa.Column("rerank_score", sa.Float),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_evidence_items_query_rank", "evidence_items", ["query_id", "rank"])

    # ── audit_events (INSERT-only, no UPDATE/DELETE) ─────────────
    op.create_table(
        "audit_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "query_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("query_runs.id")
        ),
        sa.Column("sequence_no", sa.BigInteger, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("canonical_payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("previous_hash", sa.Text),
        sa.Column("event_hash", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "sequence_no"),
    )
    op.create_index("ix_audit_events_tenant_seq", "audit_events", ["tenant_id", "sequence_no"])
    op.create_index("ix_audit_events_query", "audit_events", ["query_id"])

    # ── dead_letter_tasks ─────────────────────────────────────────
    op.create_table(
        "dead_letter_tasks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("task_type", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("external_object_id", sa.Text),
        sa.Column("source_revision", sa.Text),
        sa.Column("error_code", sa.Text, nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "first_failure",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_failure", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_dead_letter_tenant", "dead_letter_tasks", ["tenant_id"])
    op.create_index("ix_dead_letter_error", "dead_letter_tasks", ["error_code"])

    # ── Row-Level Security on all tenant-scoped tables ───────────
    tenant_tables = [
        "users",
        "source_connections",
        "encrypted_credentials",
        "identity_links",
        "source_objects",
        "object_versions",
        "chunks",
        "principals",
        "acl_grants",
        "ingestion_cursors",
        "webhook_deliveries",
        "query_runs",
        "access_decisions",
        "evidence_items",
        "audit_events",
        "dead_letter_tasks",
    ]
    for table in tenant_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # USING filters SELECT/UPDATE/DELETE; WITH CHECK gates INSERT/UPDATE.
        # Without WITH CHECK, RLS silently drops every INSERT (0 rows).
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    # ── Revoke UPDATE/DELETE on audit_events from application role ─
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM assistant")
    op.execute("GRANT INSERT, SELECT ON audit_events TO assistant")


def downgrade() -> None:
    tables = [
        "dead_letter_tasks",
        "audit_events",
        "evidence_items",
        "access_decisions",
        "query_runs",
        "webhook_deliveries",
        "ingestion_cursors",
        "acl_grants",
        "principals",
        "chunks",
        "object_versions",
        "source_objects",
        "identity_links",
        "encrypted_credentials",
        "source_connections",
        "users",
        "tenants",
    ]
    for table in tables:
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
