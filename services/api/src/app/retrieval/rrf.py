"""Reciprocal-rank fusion (RRF) — deterministic fusion of candidate lists."""

from __future__ import annotations

from uuid import UUID

from app.domain.types import CandidateRef, Source
from app.observability.logging import get_logger
from app.retrieval.lexical import LexicalHit
from app.retrieval.vector import VectorHit
from app.settings import get_settings

logger = get_logger(__name__)


def fuse_candidates(
    vector_hits: list[VectorHit],
    lexical_hits: list[LexicalHit],
    k: int | None = None,
) -> list[CandidateRef]:
    """Fuse vector and lexical candidate lists using RRF. Deterministic."""
    settings = get_settings()
    rrf_k = k if k is not None else settings.RRF_K

    vector_ranks: dict[UUID, int] = {hit.chunk_id: idx for idx, hit in enumerate(vector_hits)}
    lexical_ranks: dict[UUID, int] = {hit.chunk_id: idx for idx, hit in enumerate(lexical_hits)}

    all_chunk_ids = set(vector_ranks.keys()) | set(lexical_ranks.keys())

    chunk_meta: dict[UUID, tuple[UUID, Source]] = {}
    for hit in vector_hits:
        chunk_meta[hit.chunk_id] = (hit.source_object_id, hit.source)
    for hit in lexical_hits:
        if hit.chunk_id not in chunk_meta:
            chunk_meta[hit.chunk_id] = (hit.source_object_id, hit.source)

    candidates: list[CandidateRef] = []
    for chunk_id in all_chunk_ids:
        source_object_id, source = chunk_meta[chunk_id]
        vector_rank = vector_ranks.get(chunk_id)
        lexical_rank = lexical_ranks.get(chunk_id)

        fused_score = 0.0
        if vector_rank is not None:
            fused_score += 1.0 / (rrf_k + vector_rank + 1)
        if lexical_rank is not None:
            fused_score += 1.0 / (rrf_k + lexical_rank + 1)

        candidates.append(
            CandidateRef(
                chunk_id=chunk_id,
                source_object_id=source_object_id,
                source=source,
                vector_rank=vector_rank,
                lexical_rank=lexical_rank,
                fused_score=fused_score,
            )
        )

    candidates.sort(key=lambda c: (-c.fused_score, str(c.chunk_id)))

    logger.info(
        "RRF fusion complete",
        vector_count=len(vector_hits),
        lexical_count=len(lexical_hits),
        fused_count=len(candidates),
    )
    return candidates
