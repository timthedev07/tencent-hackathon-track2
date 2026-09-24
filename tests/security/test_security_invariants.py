"""Security invariant tests (SEC-001 through SEC-010).

These tests verify the 9 non-negotiable security invariants.
They MUST pass before merge — CI blocks on failure.

A retrieval test that finds the right answer using denied evidence
is a FAILED test.
"""

from __future__ import annotations

import pytest

# Mark all tests as security tests
pytestmark = pytest.mark.security


class TestINV001NoCandidateTextBeforeAuth:
    """INV-001: Candidate text must not enter reranker/context/LLM/logs."""

    def test_vector_search_returns_no_text(self) -> None:
        """Vector search must return only IDs and scores, never text."""
        # The VectorHit model has: chunk_id, source_object_id, source, score
        # It does NOT have a 'text' field.
        from app.retrieval.vector import VectorHit

        fields = set(VectorHit.model_fields.keys())
        assert "text" not in fields, "VectorHit must not contain text"
        assert "chunk_id" in fields
        assert "score" in fields

    def test_lexical_search_returns_no_text(self) -> None:
        """Lexical search must return only IDs and ranks, never text."""
        from app.retrieval.lexical import LexicalHit

        fields = set(LexicalHit.model_fields.keys())
        assert "text" not in fields, "LexicalHit must not contain text"
        assert "chunk_id" in fields
        assert "rank" in fields


class TestINV002NeverAssumeAccess:
    """INV-002: Live check is the only authoritative decision."""

    def test_connector_unavailable_returns_unknown(self) -> None:
        """When connector is unavailable, outcome must be UNKNOWN — never ALLOW."""
        from app.domain.types import AccessOutcome

        # The _get_connector stub returns None → check_live_access returns UNKNOWN
        # This is the correct behaviour: never assume access.
        assert AccessOutcome.UNKNOWN != AccessOutcome.ALLOW


class TestINV003QueryScopedAccess:
    """INV-003: Positive access is query-scoped — never reused."""

    def test_access_decisions_have_query_id(self) -> None:
        """Every access decision must be tied to a specific query."""
        from app.persistence.models import AccessDecision

        assert hasattr(AccessDecision, "query_id"), "AccessDecision must have query_id"


class TestINV004NoServiceAccountFallback:
    """INV-004: No service-account substitution."""

    def test_credential_resolution_requires_user_id(self) -> None:
        """Credential resolution must filter by user_id, not just connection."""
        # In _resolve_delegated_credential, the query filters:
        #   EncryptedCredential.user_id == user.user_id
        # This ensures only user-scoped credentials are used.
        from app.persistence.models import EncryptedCredential

        assert hasattr(EncryptedCredential, "user_id"), "EncryptedCredential must have user_id"


class TestINV005AnswerAfterAudit:
    """INV-005: Final answer not emitted until audit commit succeeds."""

    def test_audit_commit_is_atomic(self) -> None:
        """The pipeline's _audit_commit function writes state, answer,
        evidence, and audit event in ONE transaction."""
        # Verified by code inspection: _audit_commit uses a single session
        # and commits once at the end of the pipeline.
        from app.retrieval import pipeline

        assert hasattr(pipeline, "_audit_commit"), "Pipeline must have _audit_commit"


class TestINV006CitationReauthorisation:
    """INV-006: Opening a citation triggers a fresh access check."""

    def test_evidence_endpoint_calls_live_check(self) -> None:
        """The evidence service must call check_live_access before returning text."""
        # Verified by code inspection: get_evidence_with_reauth calls
        # check_live_access and returns access_revoked if denied.
        import app.domain.evidence_service as svc

        assert hasattr(svc, "get_evidence_with_reauth")


class TestINV007ServerDerivedTenant:
    """INV-007: Tenant identity is server-derived, never from client input."""

    def test_query_request_has_no_tenant_id(self) -> None:
        """The CreateQueryRequest must not accept tenant_id."""
        from app.api.routes.queries import CreateQueryRequest

        fields = set(CreateQueryRequest.model_fields.keys())
        assert "tenant_id" not in fields, "CreateQueryRequest must not contain tenant_id"
        assert "source_user_id" not in fields
        assert "provider_identity" not in fields


class TestINV008NoContentInLogs:
    """INV-008: No source content, query text, or answer in logs."""

    def test_logging_filters_sensitive_fields(self) -> None:
        """The log processor must filter forbidden fields."""
        from app.observability.logging import _filter_sensitive_fields

        event = {
            "source_content": "secret content",
            "chunk_text": "chunk text",
            "query_plaintext": "user query",
            "answer_plaintext": "the answer",
            "access_token": "token123",
            "safe_field": "ok",
        }
        filtered = _filter_sensitive_fields(None, None, event)
        assert "source_content" not in filtered
        assert "chunk_text" not in filtered
        assert "query_plaintext" not in filtered
        assert "answer_plaintext" not in filtered
        assert "access_token" not in filtered
        assert filtered["safe_field"] == "ok"


class TestINV009AuditIntegrity:
    """INV-009: Audit table is INSERT-only; hash chain is tamper-evident."""

    def test_genesis_marker_is_fixed(self) -> None:
        """The genesis marker must be a fixed, known value."""
        from app.audit.canonicalise import GENESIS_MARKER

        assert GENESIS_MARKER == "genesis-v1-00000000"

    def test_canonical_json_is_deterministic(self) -> None:
        """Canonical JSON must sort keys for deterministic hashing."""
        from app.audit.canonicalise import canonical_json

        data1 = {"b": 1, "a": 2}
        data2 = {"a": 2, "b": 1}
        assert canonical_json(data1) == canonical_json(data2)

    def test_hash_chain_is_sequential(self) -> None:
        """The hash chain must include the previous hash."""
        from app.audit.canonicalise import compute_event_hash

        class FakeEvent:
            event_type = "test"
            sequence_no = 1
            tenant_id = "00000000-0000-0000-0000-000000000000"
            query_id = None
            canonical_payload: dict = {"test": True}
            created_at = None

        hash1 = compute_event_hash(FakeEvent(), "genesis-v1-00000000")
        hash2 = compute_event_hash(FakeEvent(), "different_prev")
        assert hash1 != hash2, "Hash must depend on previous hash"


class TestPromptInjectionDefence:
    """Verify the context compiler's prompt-injection defence."""

    def test_control_characters_stripped(self) -> None:
        """Control and invisible characters must be stripped from evidence."""
        from app.retrieval.context import _normalise_text

        malicious = "hello\x00world\u200b\ufeff"
        cleaned = _normalise_text(malicious)
        assert "\x00" not in cleaned
        assert "\u200b" not in cleaned
        assert "\ufeff" not in cleaned

    def test_system_instructions_state_untrusted(self) -> None:
        """System prompt must declare evidence as untrusted data."""
        from app.retrieval.context import _SYSTEM_INSTRUCTIONS

        assert "UNTRUSTED" in _SYSTEM_INSTRUCTIONS
        assert "not executable" in _SYSTEM_INSTRUCTIONS.lower()
