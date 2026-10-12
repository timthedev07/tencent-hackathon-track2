"""HNSW underfill fallback — iterative scan → widen lexical → exact vector scan.

When HNSW returns too few results, progressively widen the search:
1. Increase HNSW ef_search parameter
2. Widen lexical search (relax query terms)
3. Fall back to exact vector scan (no index)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.source_mode import active_connection_mode
from app.domain.types import Source
from app.observability.logging import get_logger
from app.observability.metrics import RETRIEVAL_UNDERFILL
from app.persistence.models import Chunk, ObjectVersion, SourceConnection, SourceObject
from app.retrieval.lexical import LexicalHit, lexical_search
from app.retrieval.temporal import active_at
from app.retrieval.vector import VectorHit, vector_search
from app.settings import get_settings

logger = get_logger(__name__)

_MIN_CANDIDATES = 20  # Minimum acceptable candidate count before underfill


async def vector_search_with_fallback(
    session: AsyncSession,
    tenant_id: UUID,
    query_embedding: list[float],
    top_k: int | None = None,
    as_of: datetime | None = None,
) -> list[VectorHit]:
    """Vector search with underfill fallback.

    1. Try HNSW index search
    2. If underfilled, increase ef_search and retry
    3. If still underfilled, fall back to exact scan (no index)
    """
    settings = get_settings()
    limit = top_k or settings.CANDIDATE_TOP_K

    # Attempt 1: standard HNSW search
    hits = await vector_search(session, tenant_id, query_embedding, limit, as_of)

    if len(hits) >= _MIN_CANDIDATES:
        return hits

    logger.info(
        "HNSW underfill detected — attempting wider search",
        initial_count=len(hits),
        min_required=_MIN_CANDIDATES,
    )
    RETRIEVAL_UNDERFILL.labels(source="vector").inc()

    # Attempt 2: increase ef_search via SET
    await session.execute(text("SET LOCAL hnsw.ef_search = 200"))
    hits = await vector_search(session, tenant_id, query_embedding, limit, as_of)

    if len(hits) >= _MIN_CANDIDATES:
        return hits

    RETRIEVAL_UNDERFILL.labels(source="vector_exact").inc()

    # Attempt 3: exact scan (sequential, no index)
    distance = Chunk.embedding.cosine_distance(query_embedding)
    temporal_filters = (
        [
            SourceObject.is_queryable.is_(True),
            SourceObject.is_deleted.is_(False),
            SourceObject.current_version_id == ObjectVersion.id,
            ObjectVersion.retired_at.is_(None),
        ]
        if as_of is None
        else [active_at(ObjectVersion, as_of)]
    )
    stmt = (
        select(
            Chunk.id,
            SourceObject.id,
            SourceObject.source,
            distance.label("distance"),
        )
        .join(ObjectVersion, Chunk.object_version_id == ObjectVersion.id)
        .join(SourceObject, ObjectVersion.source_object_id == SourceObject.id)
        .join(SourceConnection, SourceObject.source_connection_id == SourceConnection.id)
        .where(
            Chunk.tenant_id == tenant_id,
            SourceObject.tenant_id == tenant_id,
            SourceConnection.mode == active_connection_mode(),
            ObjectVersion.ingestion_status == "active",
            Chunk.embedding.isnot(None),
            *temporal_filters,
        )
        .order_by(distance.asc())
        .limit(limit)
    )

    result = await session.execute(stmt)
    rows = result.all()

    exact_hits = [
        VectorHit(
            chunk_id=row[0],
            source_object_id=row[1],
            source=Source(row[2]),
            score=1.0 - row[3],
        )
        for row in rows
    ]

    logger.info(
        "Exact vector scan fallback complete",
        exact_count=len(exact_hits),
    )
    return exact_hits if len(exact_hits) > len(hits) else hits


async def lexical_search_with_fallback(
    session: AsyncSession,
    tenant_id: UUID,
    query_text: str,
    top_k: int | None = None,
    as_of: datetime | None = None,
) -> list[LexicalHit]:
    """Lexical search with query widening fallback.

    1. Try exact phrase search
    2. If underfilled, try OR-separated terms
    3. If still underfilled, try prefix matching
    """
    settings = get_settings()
    limit = top_k or settings.CANDIDATE_TOP_K

    hits = await lexical_search(session, tenant_id, query_text, limit, as_of)

    if len(hits) >= _MIN_CANDIDATES:
        return hits

    RETRIEVAL_UNDERFILL.labels(source="lexical").inc()

    # Widen: use OR-separated individual terms
    terms = query_text.split()
    if len(terms) > 1:
        widened_query = " | ".join(terms)
        widened_hits = await lexical_search(session, tenant_id, widened_query, limit, as_of)
        if len(widened_hits) > len(hits):
            return widened_hits

    return hits
