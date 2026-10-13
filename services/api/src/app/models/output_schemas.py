"""Output schemas for structured model generation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class GeneratedClaim(BaseModel):
    """A single claim in a generated answer."""

    model_config = ConfigDict(extra="forbid")

    text: str
    # The model cites the short, human-readable ``evi_...`` identifiers in
    # the prompt.  The gateway maps them back to internal UUIDs before the
    # answer enters the domain verifier.
    evidence_ids: list[str]


class GeneratedAnswer(BaseModel):
    """Structured output schema for model generation."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    claims: list[GeneratedClaim]

    @classmethod
    def openai_json_schema(cls) -> dict[str, Any]:
        """Return the provider-safe strict schema used by Chat Completions.

        Pydantic's schema is useful for validation, but it can contain `$defs`
        and `$ref` entries.  Keeping the wire schema explicitly inlined avoids
        provider-specific rejection of otherwise valid Pydantic metadata.
        """
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string"},
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string"},
                            "evidence_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["text", "evidence_ids"],
                    },
                },
            },
            "required": ["answer", "claims"],
        }
