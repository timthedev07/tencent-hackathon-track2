"""Context compiler tests — prompt-injection defence."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.domain.types import AuthorisedEvidence, RerankedEvidence, Source
from app.retrieval.context import _SYSTEM_INSTRUCTIONS, _normalise_text, compile_context
from app.retrieval.pipeline import _reranked_for_compiled_context


def _evidence(
    text: str,
    *,
    source_object_id: UUID | None = None,
    chunk_ordinal: int | None = None,
) -> AuthorisedEvidence:
    return AuthorisedEvidence(
        evidence_id=uuid4(),
        chunk_id=uuid4(),
        source_object_id=source_object_id or uuid4(),
        source=Source.GOOGLE_DRIVE,
        source_revision="rev1",
        title="Q3 payment gateway outage",
        text=text,
        fused_score=0.9,
        chunk_ordinal=chunk_ordinal,
    )


def test_normalise_strips_control_chars() -> None:
    assert _normalise_text("hello\x00\x01\x02world") == "helloworld"


def test_normalise_strips_invisible_chars() -> None:
    assert _normalise_text("a\u200bb\u200fc") == "abc"


def test_system_instructions_warn_about_injection() -> None:
    assert "UNTRUSTED" in _SYSTEM_INSTRUCTIONS
    assert "not executable" in _SYSTEM_INSTRUCTIONS.lower()


def test_compile_context_respects_token_budget() -> None:
    evidence = [_evidence("x" * 1000)]
    result = compile_context(evidence, max_tokens=100)
    # Should include at most 1 evidence block given the small budget
    assert len(result.evidence_blocks) <= 1


def test_compile_context_skips_oversized_block_and_considers_later_evidence() -> None:
    oversized = _evidence("x" * 5000)
    useful = _evidence("Customer impact: 428 failed checkout attempts.")

    result = compile_context([oversized, useful], max_tokens=1000)

    assert result.evidence_blocks == [useful]


def test_compile_context_keeps_adjacent_document_chunks_in_source_order() -> None:
    source_object_id = uuid4()
    impact = _evidence(
        "Customer impact: 428 failed checkout attempts.",
        source_object_id=source_object_id,
        chunk_ordinal=1,
    )
    summary = _evidence(
        "The Q3 payment gateway outage lasted 47 minutes.",
        source_object_id=source_object_id,
        chunk_ordinal=0,
    )
    unrelated = _evidence("A separate operational note.", chunk_ordinal=0)

    result = compile_context([impact, unrelated, summary], max_tokens=2000)

    assert result.evidence_blocks == [summary, impact, unrelated]


def test_citation_metadata_contains_only_compiled_evidence_in_prompt_order() -> None:
    first = _evidence("First included block.")
    excluded = _evidence("Excluded block.")
    second = _evidence("Second included block.")
    reranked = [
        RerankedEvidence(evidence=first, rerank_score=0.9, rank=0),
        RerankedEvidence(evidence=excluded, rerank_score=0.8, rank=1),
        RerankedEvidence(evidence=second, rerank_score=0.7, rank=2),
    ]

    included = _reranked_for_compiled_context(reranked, [second, first])

    assert [item.evidence for item in included] == [second, first]
    assert [item.rank for item in included] == [0, 1]
    assert excluded.evidence_id not in {item.evidence.evidence_id for item in included}
