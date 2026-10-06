"""Domain types — enums and core value objects.

These types are the source of truth for the domain layer.
No provider SDK or HTTP payload types shall leak into this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ── Enumerations ────────────────────────────────────────────────────


class Source(StrEnum):
    """Supported enterprise knowledge sources."""

    CONFLUENCE = "confluence"
    JIRA = "jira"
    SLACK = "slack"
    GOOGLE_DRIVE = "google_drive"


class QueryStatus(StrEnum):
    """Query lifecycle states."""

    CREATED = "created"
    AUTHENTICATED = "authenticated"
    CANDIDATES = "candidates"
    REAUTHORISED = "reauthorised"
    RERANKED = "reranked"
    COMPILED = "compiled"
    GENERATED = "generated"
    VERIFIED = "verified"
    AUDITED = "audited"
    COMPLETED = "completed"
    PARTIAL = "partial"
    DENIED = "denied"
    FAILED = "failed"


class IngestionStatus(StrEnum):
    """Ingestion pipeline states for object versions."""

    RECEIVED = "received"
    FETCHED = "fetched"
    NORMALISED = "normalised"
    ACL_RESOLVED = "acl_resolved"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class AccessOutcome(StrEnum):
    """Outcome of a live provider access check."""

    ALLOW = "allow"
    DENY = "deny"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    EXPIRED_CREDENTIAL = "expired_credential"
    IDENTITY_MISSING = "identity_missing"


class ConnectorOutcome(StrEnum):
    """Typed outcome for every connector operation."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_ERROR = "transient_error"
    PERMANENT_ERROR = "permanent_error"


class CredentialType(StrEnum):
    """Types of stored provider credentials."""

    OAUTH_REFRESH = "oauth_refresh"
    OAUTH_ACCESS = "oauth_access"
    SERVICE_ACCOUNT = "service_account"


class IdentityLinkStatus(StrEnum):
    """Status of a source identity link."""

    VERIFIED = "verified"
    PENDING = "pending"
    BROKEN = "broken"


class SourceConnectionStatus(StrEnum):
    """Status of a tenant's source connection."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


# ── Domain Value Objects ────────────────────────────────────────────


class SourceConnection(BaseModel):
    """Tenant-level provider connection."""

    id: UUID
    tenant_id: UUID
    source: Source
    external_workspace_id: str
    display_name: str | None = None
    status: SourceConnectionStatus = SourceConnectionStatus.CONNECTED
    last_sync_at: datetime | None = None
    last_reconcile_at: datetime | None = None


class DelegatedCredential(BaseModel):
    """In-memory credential for a live provider access check.

    This must be discarded after use — never logged or persisted in plaintext.
    """

    source: Source
    access_token: str = Field(repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    expires_at: datetime | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateRef(BaseModel):
    """Candidate reference — IDs and ranking only, no text."""

    chunk_id: UUID
    source_object_id: UUID
    source: Source
    vector_rank: int | None = None
    lexical_rank: int | None = None
    fused_score: float = 0.0


class AuthorisedEvidence(BaseModel):
    """First application-level object that may contain evidence text."""

    evidence_id: UUID
    chunk_id: UUID
    source_object_id: UUID
    source: Source
    source_revision: str
    title: str | None = None
    canonical_url: str | None = None
    text: str = ""
    fused_score: float = 0.0


class GeneratedClaim(BaseModel):
    """A single claim in a generated answer."""

    text: str
    evidence_ids: list[UUID] = Field(default_factory=list)


class GeneratedAnswer(BaseModel):
    """Structured output schema for model generation."""

    answer: str
    claims: list[GeneratedClaim]


class RerankedEvidence(BaseModel):
    """Authorised evidence with a rerank score."""

    evidence: AuthorisedEvidence
    rerank_score: float
    rank: int


class CompiledContext(BaseModel):
    """Deterministic, token-budgeted context for the model."""

    evidence_blocks: list[AuthorisedEvidence]
    system_prompt: str
    total_token_count: int
    query_text: str = ""


# ── Connector result types ──────────────────────────────────────────


class ScanPage(BaseModel):
    """A page of results from an initial scan."""

    objects: list[dict[str, Any]]
    next_cursor: str | None = None


class ChangePage(BaseModel):
    """A page of incremental changes."""

    changes: list[dict[str, Any]]
    next_cursor: str | None = None
    has_deletions: bool = False


class FetchObjectResult(BaseModel):
    """Result of fetching a single object."""

    outcome: ConnectorOutcome
    external_object_id: str
    source_revision: str | None = None
    object_type: str | None = None
    parent_external_id: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    content: str | None = None
    last_modified_at: datetime | None = None
    permission_principals: list[dict[str, Any]] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class FetchAclResult(BaseModel):
    """Result of fetching ACL grants for an object."""

    outcome: ConnectorOutcome
    grants: list[dict[str, Any]] = Field(default_factory=list)


class AccessCheckResult(BaseModel):
    """Result of a live provider access check."""

    outcome: AccessOutcome
    reason_code: str | None = None
    provider_request_id: str | None = None
    latency_ms: int | None = None


# ── Normalisation types ────────────────────────────────────────────


class ContentBlock(BaseModel):
    """A structural content block with stable identity."""

    block_id: str
    kind: str
    text: str
    section_path: list[str] = Field(default_factory=list)
    author_id: str | None = None
    created_at: datetime | None = None


class NormalisedObject(BaseModel):
    """Provider-agnostic normalised representation of a source object."""

    source: Source
    external_object_id: str
    source_revision: str
    object_type: str
    parent_external_id: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    last_modified_at: datetime | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


# ── Audit types ─────────────────────────────────────────────────────


class AuditEventPayload(BaseModel):
    """Canonical payload for an audit event."""

    event_type: str
    query_id: UUID | None = None
    user_id: UUID | None = None
    tenant_id: UUID
    data: dict[str, Any] = Field(default_factory=dict)
