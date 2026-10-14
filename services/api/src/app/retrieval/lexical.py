"""Lexical search via PostgreSQL full-text search (tsvector).

INV-001: Returns only chunk IDs and ranks — no text content.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.types import Source
from app.observability.logging import get_logger
from app.persistence.models import Chunk, ObjectVersion, SourceConnection, SourceObject
from app.settings import get_settings

logger = get_logger(__name__)


class LexicalHit(BaseModel):
    """Lexical search result — IDs and rank only, no text."""

    chunk_id: UUID
    source_object_id: UUID
    source: Source
    rank: int


async def lexical_search(
    session: AsyncSession,
    tenant_id: UUID,
    query_text: str,
    top_k: int | None = None,
) -> list[LexicalHit]:
    """Search chunks using PostgreSQL tsvector full-text search."""
    settings = get_settings()
    limit = top_k or settings.CANDIDATE_TOP_K

    rank_expr = func.ts_rank(
        Chunk.tsv,
        func.plainto_tsquery("english", query_text),
    )
    stmt = (
        select(
            Chunk.id,
            SourceObject.id,
            SourceObject.source,
            rank_expr.label("rank_score"),
        )
        .join(ObjectVersion, Chunk.object_version_id == ObjectVersion.id)
        .join(SourceObject, ObjectVersion.source_object_id == SourceObject.id)
        .join(SourceConnection, SourceObject.source_connection_id == SourceConnection.id)
        .where(
            Chunk.tenant_id == tenant_id,
            SourceObject.tenant_id == tenant_id,
            SourceObject.is_queryable.is_(True),
            SourceObject.is_deleted.is_(False),
            SourceObject.current_version_id == ObjectVersion.id,
            ObjectVersion.ingestion_status == "active",
            ObjectVersion.retired_at.is_(None),
            Chunk.tsv.isnot(None),
        )
        .order_by(rank_expr.desc())
        .limit(limit)
    )

    result = await session.execute(stmt)
    rows = result.all()

    hits = [
        LexicalHit(
            chunk_id=row[0],
            source_object_id=row[1],
            source=Source(row[2]),
            rank=idx,
        )
        for idx, row in enumerate(rows)
    ]

    logger.info(
        "Lexical search complete",
        tenant_id=str(tenant_id),
        candidate_count=len(hits),
    )
    return hits
