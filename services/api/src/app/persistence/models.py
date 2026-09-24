"""SQLAlchemy ORM models for all 17 database tables.

All tenant-scoped tables use RLS (Row-Level Security).
Audit table is INSERT-only (no UPDATE/DELETE for application role).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.settings import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    external_org_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (UniqueConstraint("external_org_key"),)

    users: Mapped[list[User]] = relationship(back_populates="tenant")
    source_connections: Mapped[list[SourceConnection]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    oidc_subject: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    is_compliance_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (UniqueConstraint("tenant_id", "oidc_subject"),)

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    identity_links: Mapped[list[IdentityLink]] = relationship(back_populates="user")


class SourceConnection(Base):  # type: ignore[misc]
    __tablename__ = "source_connections"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="connected")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="source_connections")
    credentials: Mapped[list[EncryptedCredential]] = relationship(back_populates="connection")
    source_objects: Mapped[list[SourceObject]] = relationship(back_populates="connection")


class EncryptedCredential(Base):
    __tablename__ = "encrypted_credentials"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[str | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    source_connection_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_connections.id"), nullable=False
    )
    credential_type: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    connection: Mapped[SourceConnection] = relationship(back_populates="credentials")


class IdentityLink(Base):
    __tablename__ = "identity_links"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    source_connection_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_connections.id"), nullable=False
    )
    provider_subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_account_type: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    verified_by: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="verified")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("source_connection_id", "provider_subject_id"),
        UniqueConstraint("user_id", "source_connection_id"),
    )

    user: Mapped[User] = relationship(back_populates="identity_links")


class SourceObject(Base):
    __tablename__ = "source_objects"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_connection_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_connections.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_object_id: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    parent_external_id: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    current_revision: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[str | None] = mapped_column(PGUUID(as_uuid=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_queryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "external_object_id"),
        Index("ix_source_objects_tenant_source_ext", "tenant_id", "source", "external_object_id"),
        Index("ix_source_objects_tenant_queryable", "tenant_id", "is_queryable"),
        Index("ix_source_objects_tenant_conn", "tenant_id", "source_connection_id"),
    )

    connection: Mapped[SourceConnection] = relationship(back_populates="source_objects")
    versions: Mapped[list[ObjectVersion]] = relationship(back_populates="source_object")


class ObjectVersion(Base):
    __tablename__ = "object_versions"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_object_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_objects.id"), nullable=False
    )
    source_revision: Mapped[str] = mapped_column(Text, nullable=False)
    normalised_title: Mapped[str | None] = mapped_column(Text)
    normalised_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_status: Mapped[str] = mapped_column(Text, nullable=False, default="received")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("source_object_id", "source_revision"),
        Index("ix_object_versions_obj_rev", "source_object_id", "source_revision"),
        Index("ix_object_versions_tenant_observed", "tenant_id", text("observed_at DESC")),
        Index("ix_object_versions_tenant_status", "tenant_id", "ingestion_status"),
    )

    source_object: Mapped[SourceObject] = relationship(back_populates="versions")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="version")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    object_version_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("object_versions.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(get_settings().EMBEDDING_DIMENSION)
    )
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("object_version_id", "ordinal"),
        Index("ix_chunks_tenant_version", "tenant_id", "object_version_id"),
        Index("ix_chunks_tsv", text("tsv"), postgresql_using="gin"),
    )

    version: Mapped[ObjectVersion] = relationship(back_populates="chunks")


class Principal(Base):
    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_connection_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_connections.id"), nullable=False
    )
    principal_type: Mapped[str] = mapped_column(Text, nullable=False)
    provider_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AclGrant(Base):
    __tablename__ = "acl_grants"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_object_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_objects.id"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id"), nullable=False
    )
    permission: Mapped[str] = mapped_column(Text, nullable=False)
    grant_effect: Mapped[str] = mapped_column(Text, nullable=False)
    inheritance_path: Mapped[dict | None] = mapped_column(JSONB)
    source_revision: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_acl_grants_tenant_obj", "tenant_id", "source_object_id"),
        Index("ix_acl_grants_tenant_principal", "tenant_id", "principal_id"),
        Index("ix_acl_grants_obj_perm", "source_object_id", "permission"),
    )


class IngestionCursor(Base):
    __tablename__ = "ingestion_cursors"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_connection_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_connections.id"), nullable=False
    )
    cursor_type: Mapped[str] = mapped_column(Text, nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    provider_delivery_id: Mapped[str] = mapped_column(Text, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="received")
    payload_hash: Mapped[str | None] = mapped_column(Text)
    safe_metadata: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (UniqueConstraint("source", "provider_delivery_id"),)


class QueryRun(Base):
    __tablename__ = "query_runs"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    query_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="created")
    partial_sources: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    prompt_version: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    retriever_version: Mapped[str | None] = mapped_column(Text)
    reranker_version: Mapped[str | None] = mapped_column(Text)
    context_policy_version: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    answer_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)

    __table_args__ = (
        Index("ix_query_runs_tenant_user_started", "tenant_id", "user_id", text("started_at DESC")),
        Index("ix_query_runs_tenant_status", "tenant_id", "status"),
    )

    access_decisions: Mapped[list[AccessDecision]] = relationship(back_populates="query")
    evidence_items: Mapped[list[EvidenceItem]] = relationship(back_populates="query")


class AccessDecision(Base):
    __tablename__ = "access_decisions"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    query_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("query_runs.id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_object_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_objects.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    access_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    provider_latency_ms: Mapped[int | None] = mapped_column(Integer)
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_access_decisions_query", "query_id"),
        Index("ix_access_decisions_tenant_obj", "tenant_id", "source_object_id"),
    )

    query: Mapped[QueryRun] = relationship(back_populates="access_decisions")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    query_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("query_runs.id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chunks.id"), nullable=False
    )
    source_object_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_objects.id"), nullable=False
    )
    source_revision: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    title_snapshot: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    quoted_span_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_score: Mapped[float | None] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_evidence_items_query_rank", "query_id", "rank"),)

    query: Mapped[QueryRun] = relationship(back_populates="evidence_items")


class AuditEvent(Base):
    """Append-only audit events with tamper-evident hash chain.

    Application role: INSERT + scoped SELECT only.
    UPDATE and DELETE are denied at the database level.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    query_id: Mapped[str | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("query_runs.id"))
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(Text)
    event_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "sequence_no"),
        Index("ix_audit_events_tenant_seq", "tenant_id", "sequence_no"),
        Index("ix_audit_events_query", "query_id"),
    )


class DeadLetterTask(Base):
    """Dead-letter entries for failed ingestion tasks (admin-only visibility)."""

    __tablename__ = "dead_letter_tasks"

    id: Mapped[str] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    external_object_id: Mapped[str | None] = mapped_column(Text)
    source_revision: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failure: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_failure: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_dead_letter_tenant", "tenant_id"),
        Index("ix_dead_letter_error", "error_code"),
    )
