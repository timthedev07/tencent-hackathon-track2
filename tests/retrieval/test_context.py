"""Context compiler tests — prompt-injection defence."""

from __future__ import annotations

from uuid import uuid4

from app.domain.types import AuthorisedEvidence, Source
from app.retrieval.context import _SYSTEM_INSTRUCTIONS, _normalise_text, compile_context


def test_normalise_strips_control_chars() -> None:
    assert _normalise_text("hello\x00\x01\x02world") == "helloworld"


def test_normalise_strips_invisible_chars() -> None:
    assert _normalise_text("a\u200bb\u200fc") == "abc"


def test_system_instructions_warn_about_injection() -> None:
    assert "UNTRUSTED" in _SYSTEM_INSTRUCTIONS
    assert "not executable" in _SYSTEM_INSTRUCTIONS.lower()


def test_compile_context_respects_token_budget() -> None:
    evidence = [
        AuthorisedEvidence(
            evidence_id=uuid4(),
            chunk_id=uuid4(),
            source_object_id=uuid4(),
            source=Source.JIRA,
            source_revision="rev1",
            title="Issue",
            text="x" * 1000,
            fused_score=0.9,
        )
    ]
    result = compile_context(evidence, max_tokens=100)
    # Should include at most 1 evidence block given the small budget
    assert len(result.evidence_blocks) <= 1
