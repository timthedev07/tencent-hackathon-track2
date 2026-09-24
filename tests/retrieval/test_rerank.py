"""Retrieval quality tests — reranking and context compilation."""

from __future__ import annotations

from uuid import uuid4

from app.domain.types import AuthorisedEvidence, Source
from app.retrieval.rerank import rerank_authorised_evidence


def test_rerank_preserves_order() -> None:
    """Reranking should return evidence sorted by score."""
    evidence = [
        AuthorisedEvidence(
            evidence_id=uuid4(),
            chunk_id=uuid4(),
            source_object_id=uuid4(),
            source=Source.CONFLUENCE,
            source_revision="rev1",
            title="Page 1",
            text="content",
            fused_score=0.8,
        ),
        AuthorisedEvidence(
            evidence_id=uuid4(),
            chunk_id=uuid4(),
            source_object_id=uuid4(),
            source=Source.JIRA,
            source_revision="rev2",
            title="Issue 1",
            text="content",
            fused_score=0.9,
        ),
    ]

    result = rerank_authorised_evidence(evidence, target_count=2)
    assert len(result) == 2
    assert result[0].rerank_score >= result[1].rerank_score


def test_rerank_source_diversity() -> None:
    """Reranking should cap evidence from a single source."""
    evidence = [
        AuthorisedEvidence(
            evidence_id=uuid4(),
            chunk_id=uuid4(),
            source_object_id=uuid4(),
            source=Source.JIRA,
            source_revision=f"rev{i}",
            title=f"Issue {i}",
            text=f"content {i}",
            fused_score=1.0 - i * 0.01,
        )
        for i in range(8)
    ]

    result = rerank_authorised_evidence(evidence, target_count=10)
    jira_count = sum(1 for r in result if r.evidence.source == Source.JIRA)
    # With 50% cap and target 10, max 5 from Jira
    assert jira_count <= 5
