"""Integration test for reciprocal-rank fusion."""

from __future__ import annotations

from uuid import uuid4

from app.domain.types import Source
from app.retrieval.lexical import LexicalHit
from app.retrieval.rrf import fuse_candidates
from app.retrieval.vector import VectorHit


def test_rrf_fuses_two_lists() -> None:
    """RRF must combine vector and lexical hits into a single ranked list."""
    chunk_id_1 = uuid4()
    chunk_id_2 = uuid4()
    chunk_id_3 = uuid4()

    vector_hits = [
        VectorHit(chunk_id=chunk_id_1, source_object_id=uuid4(), source=Source.JIRA, score=0.95),
        VectorHit(
            chunk_id=chunk_id_2, source_object_id=uuid4(), source=Source.CONFLUENCE, score=0.85
        ),
    ]
    lexical_hits = [
        LexicalHit(chunk_id=chunk_id_2, source_object_id=uuid4(), source=Source.CONFLUENCE, rank=0),
        LexicalHit(chunk_id=chunk_id_3, source_object_id=uuid4(), source=Source.SLACK, rank=1),
    ]

    candidates = fuse_candidates(vector_hits, lexical_hits, k=60)

    assert len(candidates) == 3
    # chunk_id_2 appears in both lists → higher RRF score
    top = candidates[0]
    assert top.chunk_id == chunk_id_2
    assert top.vector_rank is not None
    assert top.lexical_rank is not None


def test_rrf_is_deterministic() -> None:
    """RRF must produce the same output for the same input."""
    chunk_id = uuid4()
    hits_v = [VectorHit(chunk_id=chunk_id, source_object_id=uuid4(), source=Source.JIRA, score=0.9)]
    hits_l = [LexicalHit(chunk_id=chunk_id, source_object_id=uuid4(), source=Source.JIRA, rank=0)]

    result1 = fuse_candidates(hits_v, hits_l)
    result2 = fuse_candidates(hits_v, hits_l)

    assert [c.chunk_id for c in result1] == [c.chunk_id for c in result2]
