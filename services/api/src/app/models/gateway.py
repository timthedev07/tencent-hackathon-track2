"""Model gateway — abstraction over embedding, reranking, and generation.

Core domain code must never import provider SDK-specific response classes.
Map provider responses into Pydantic domain objects at the gateway boundary.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.types import (
    AuthorisedEvidence,
    CompiledContext,
    GeneratedAnswer,
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
        """Return a stub answer."""
        return GeneratedAnswer(
            answer="This is a stub answer generated in development mode.",
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
            return [item["embedding"] for item in data["data"]]

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

        evidence_text = ""
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
                },
                timeout=self._settings.QUERY_GENERATION_DEADLINE_MS / 1000,
            )
            response.raise_for_status()
            data = response.json()
            answer_text = data["choices"][0]["message"]["content"]
            return GeneratedAnswer(answer=answer_text, claims=[])


_gateway: ModelGateway | None = None


def get_model_gateway() -> ModelGateway:
    """Return the singleton model gateway."""
    global _gateway
    if _gateway is None:
        settings = get_settings()
        if settings.APP_ENV == "development" and not settings.MODEL_API_KEY:
            _gateway = StubModelGateway()
        elif settings.MODEL_API_KEY:
            _gateway = HttpModelGateway()
        else:
            _gateway = StubModelGateway()
    return _gateway
