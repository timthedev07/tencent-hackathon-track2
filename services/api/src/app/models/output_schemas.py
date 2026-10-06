"""Output schemas for structured model generation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GeneratedClaim(BaseModel):
    """A single claim in a generated answer."""

    text: str
    # The model cites the short, human-readable ``evi_...`` identifiers in
    # the prompt.  The gateway maps them back to internal UUIDs before the
    # answer enters the domain verifier.
    evidence_ids: list[str] = Field(default_factory=list)


class GeneratedAnswer(BaseModel):
    """Structured output schema for model generation."""

    answer: str
    claims: list[GeneratedClaim] = Field(default_factory=list)
