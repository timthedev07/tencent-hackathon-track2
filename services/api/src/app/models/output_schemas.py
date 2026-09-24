"""Output schemas for structured model generation."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class GeneratedClaim(BaseModel):
    """A single claim in a generated answer."""

    text: str
    evidence_ids: list[UUID] = Field(default_factory=list)


class GeneratedAnswer(BaseModel):
    """Structured output schema for model generation."""

    answer: str
    claims: list[GeneratedClaim] = Field(default_factory=list)
