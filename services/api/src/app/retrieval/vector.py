"""Vector search via pgvector — cosine similarity.

INV-001: Returns only chunk IDs and scores — no text content.
Tenant-constrained via RLS and explicit tenant_id filter.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.types import Source
from app.observability.logging import get_logger
from app.persistence.models import Chunk, ObjectVersion, SourceObject
from app.settings import get_settings

logger = get_logger(__name__)


class VectorHit(BaseModel):
    """Vector search result — IDs and score only, no text."""

    chunk_id: UUID
    source_object_id: UUID
    source: Source
    score: float


async def vector_search(
    session: AsyncSession,
    tenant_id: UUID,
    query_embedding: list[float],
    top_k: int | None = None,
) -> list[VectorHit]:
    """Search chunks by cosine similarity. Returns IDs only — never text."""
    settings = get_settings()
    limit = top_k or settings.CANDIDATE_TOP_K

    distance = Chunk.embedding.cosine_distance(query_embedding)
    stmt = (
        select(
            Chunk.id,
            SourceObject.id,
            SourceObject.source,
            distance.label("distance"),
        )
        .join(ObjectVersion, Chunk.object_version_id == ObjectVersion.id)
        .join(SourceObject, ObjectVersion.source_object_id == SourceObject.id)
        .where(
            Chunk.tenant_id == tenant_id,
            SourceObject.tenant_id == tenant_id,
            SourceObject.is_queryable.is_(True),
            SourceObject.is_deleted.is_(False),
            SourceObject.current_version_id == ObjectVersion.id,
            ObjectVersion.ingestion_status == "active",
            ObjectVersion.retired_at.is_(None),
            Chunk.embedding.isnot(None),
        )
        .order_by(distance.asc())
        .limit(limit)
    )

    result = await session.execute(stmt)
    rows = result.all()

    hits = [
        VectorHit(
            chunk_id=row[0],
            source_object_id=row[1],
            source=Source(row[2]),
            score=1.0 - row[3],
        )
        for row in rows
    ]

    logger.info(
        "Vector search complete",
        tenant_id=str(tenant_id),
        candidate_count=len(hits),
    )
    return hits
