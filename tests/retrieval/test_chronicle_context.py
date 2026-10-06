"""Chronicle temporal request and sealed-context contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.api.routes.queries import CreateQueryRequest
from app.domain.types import AuthorisedEvidence, Source
from app.models.gateway import deterministic_embedding
from app.retrieval.context import compile_context
from pydantic import ValidationError


def _evidence(content_hash: str = "sha256:one") -> AuthorisedEvidence:
    return AuthorisedEvidence(
        evidence_id=uuid4(),
        chunk_id=uuid4(),
        source_object_id=uuid4(),
        object_version_id=uuid4(),
        source=Source.JIRA,
        source_revision="2026-09-01T12:00:00Z",
        content_hash=content_hash,
        title="Payment postmortem",
        canonical_url="https://example.test/restricted-metadata",
        text="An expired mTLS certificate caused the outage.",
    )


def test_query_accepts_timezone_aware_as_of_alias() -> None:
    request = CreateQueryRequest.model_validate(
        {"query": "What happened?", "asOf": "2026-09-01T10:30:00Z"}
    )
    assert request.as_of == datetime(2026, 9, 1, 10, 30, tzinfo=UTC)


def test_query_rejects_timezone_naive_as_of() -> None:
    with pytest.raises(ValidationError):
        CreateQueryRequest.model_validate(
            {"query": "What happened?", "asOf": "2026-09-01T10:30:00"}
        )


def test_authorised_context_hash_is_deterministic_and_metadata_minimal() -> None:
    evidence = _evidence()
    as_of = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
    first = compile_context([evidence], requester="alice", as_of=as_of, query_text="What happened?")
    second = compile_context(
        [evidence], requester="alice", as_of=as_of, query_text="What happened?"
    )

    assert first.snapshot_hash == second.snapshot_hash
    assert set(first.evidence_manifest[0]) == {
        "evidence_id",
        "object_version_id",
        "content_hash",
    }
    assert "restricted-metadata" not in json.dumps(first.evidence_manifest)


def test_context_hash_changes_when_evidence_hash_changes() -> None:
    evidence = _evidence()
    first = compile_context([evidence], requester="alice")
    changed = evidence.model_copy(update={"content_hash": "sha256:two"})
    second = compile_context([changed], requester="alice")
    assert first.snapshot_hash != second.snapshot_hash


def test_local_embeddings_are_repeatable_and_nonzero() -> None:
    first = deterministic_embedding("payment outage", 64)
    second = deterministic_embedding("payment outage", 64)
    assert first == second
    assert any(value != 0 for value in first)


def test_chronicle_fixture_covers_all_sources_and_control_events() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "fixtures"
        / "chronicle"
        / "incident.json"
    )
    fixture = json.loads(fixture_path.read_text())
    assert {item["source"] for item in fixture["objects"]} == {
        "confluence",
        "jira",
        "slack",
        "google_drive",
    }
    assert {item["type"] for item in fixture["events"]} == {
        "permission_grant",
        "permission_revoke",
        "content_update",
    }
    assert any(item.get("asOf") for item in fixture["backtests"])
