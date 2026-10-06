"""Model gateway — abstraction over embedding, reranking, and generation.

Core domain code must never import provider SDK-specific response classes.
Map provider responses into Pydantic domain objects at the gateway boundary.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.domain.types import (
    AuthorisedEvidence,
    CompiledContext,
    GeneratedAnswer,
    GeneratedClaim,
    RerankedEvidence,
)
from app.observability.logging import get_logger
from app.settings import get_settings

logger = get_logger(__name__)


@runtime_checkable
class ModelGateway(Protocol):
    """Abstract model gateway — hides provider specifics."""

    async def embed(
        self,
        texts: list[str],
        model_version: str,
    ) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        ...

    async def rerank(
        self,
        query: str,
        evidence: list[AuthorisedEvidence],
    ) -> list[RerankedEvidence]:
        """Rerank authorised evidence."""
        ...

    async def generate(
        self,
        authorised_context: CompiledContext,
        output_schema: type,  # type: ignore[type-arg]
    ) -> object:
        """Generate a structured answer from the compiled context."""
        ...


class StubModelGateway:
    """Dev/stub model gateway for local development without API keys."""

    @property
    def embedding_dimension(self) -> int:
        return get_settings().EMBEDDING_DIMENSION

    async def embed(
        self,
        texts: list[str],
        model_version: str,
    ) -> list[list[float]]:
        """Return zero vectors for development."""
        return [[0.0] * self.embedding_dimension for _ in texts]

    async def rerank(
        self,
        query: str,
        evidence: list[AuthorisedEvidence],
    ) -> list[RerankedEvidence]:
        """Return evidence in original order."""
        return [
            RerankedEvidence(
                evidence=e,
                rerank_score=e.fused_score,
                rank=idx,
            )
            for idx, e in enumerate(evidence)
        ]

    async def generate(
        self,
        authorised_context: CompiledContext,
        output_schema: type,  # type: ignore[type-arg]
    ) -> object:
        """Return a deterministic, citation-bearing development answer."""
        if authorised_context.evidence_blocks:
            evidence = authorised_context.evidence_blocks[0]
            sentence = evidence.text.strip().split(". ", 1)[0].strip()
            if sentence and not sentence.endswith((".", "!", "?")):
                sentence += "."
            fallback_answer = (
                "The authorised evidence does not contain enough detail to answer this question."
            )
            return GeneratedAnswer(
                answer=sentence
                or fallback_answer,
                claims=[GeneratedClaim(text=sentence, evidence_ids=[evidence.evidence_id])]
                if sentence
                else [],
            )
        return GeneratedAnswer(
            answer="The authorised evidence is insufficient to answer this question.",
            claims=[],
        )


class HttpModelGateway:
    """Production model gateway using httpx to call model APIs."""

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def embedding_dimension(self) -> int:
        return self._settings.EMBEDDING_DIMENSION

    async def embed(
        self,
        texts: list[str],
        model_version: str,
    ) -> list[list[float]]:
        """Call the embedding API."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self._settings.MODEL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_version,
                    "input": texts,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            vectors = [item["embedding"] for item in data["data"]]
            invalid = [len(vector) for vector in vectors if len(vector) != self.embedding_dimension]
            if invalid:
                raise ValueError("INGESTION_EMBED_FAILED: embedding dimension mismatch")
            return vectors

    async def rerank(
        self,
        query: str,
        evidence: list[AuthorisedEvidence],
    ) -> list[RerankedEvidence]:
        """Rerank using the model gateway (uses fused score as fallback)."""
        return [
            RerankedEvidence(evidence=e, rerank_score=e.fused_score, rank=idx)
            for idx, e in enumerate(evidence)
        ]

    async def generate(
        self,
        authorised_context: CompiledContext,
        output_schema: type,  # type: ignore[type-arg]
    ) -> object:
        """Call the generation API with structured output."""
        import httpx

        evidence_text = f"<question>\n{authorised_context.query_text}\n</question>\n\n"
        for ev in authorised_context.evidence_blocks:
            evi_id = f"evi_{str(ev.evidence_id).replace('-', '')[:12]}"
            evidence_text += (
                f'<evidence id="{evi_id}" source="{ev.source.value}">\n{ev.text}\n</evidence>\n'
            )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._settings.MODEL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.GENERATION_MODEL,
                    "messages": [
                        {"role": "system", "content": authorised_context.system_prompt},
                        {"role": "user", "content": evidence_text},
                    ],
                    "temperature": 0.1,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "generated_answer",
                            "strict": True,
                            "schema": output_schema.model_json_schema(),
                        },
                    },
                },
                timeout=self._settings.QUERY_GENERATION_DEADLINE_MS / 1000,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("MODEL_OUTPUT_INVALID")
            schema_type: Any = output_schema
            parsed: Any = schema_type.model_validate_json(content)
            evidence_ids = {
                f"evi_{str(evidence.evidence_id).replace('-', '')[:12]}": evidence.evidence_id
                for evidence in authorised_context.evidence_blocks
            }
            mapped_claims: list[GeneratedClaim] = []
            for claim in parsed.claims:
                mapped_claims.append(
                    GeneratedClaim(
                        text=claim.text,
                        evidence_ids=[
                            evidence_ids[citation]
                            for citation in claim.evidence_ids
                            if citation in evidence_ids
                        ],
                    )
                )
            return GeneratedAnswer(
                answer=parsed.answer,
                claims=mapped_claims,
            )


_gateway: ModelGateway | None = None


def get_model_gateway() -> ModelGateway:
    """Return the singleton model gateway."""
    global _gateway
    if _gateway is None:
        settings = get_settings()
        if settings.MODEL_API_KEY:
            _gateway = HttpModelGateway()
        elif settings.is_development:
            _gateway = StubModelGateway()
        else:
            raise RuntimeError("MODEL_API_KEY is required outside development")
    return _gateway
