"""Focused tests for authorised evidence loading and prompt citation alignment."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID, uuid4

from app.domain.types import CandidateRef, Source
from app.retrieval.pipeline import _load_authorised_text


class _RowsResult:
    def __init__(self, rows: Sequence[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class _Session:
    def __init__(self, rows: Sequence[tuple[object, ...]]) -> None:
        self._rows = rows
        self.statement: Any | None = None

    async def execute(self, _statement: object) -> _RowsResult:
        self.statement = _statement
        return _RowsResult(self._rows)


async def test_load_authorised_text_preserves_all_matching_chunks_by_relevance() -> None:
    source_object_id = uuid4()
    object_version_id = uuid4()
    chunk_ids = [uuid4(), uuid4(), uuid4()]
    ranked_chunk_ids = [chunk_ids[1], chunk_ids[2], chunk_ids[0]]
    scores = [0.9, 0.8, 0.7]
    candidates = [
        CandidateRef(
            chunk_id=chunk_id,
            source_object_id=source_object_id,
            source=Source.GOOGLE_DRIVE,
            fused_score=score,
        )
        for chunk_id, score in zip(ranked_chunk_ids, scores, strict=True)
    ]
    unauthorised_chunk_id = uuid4()
    candidates.insert(
        0,
        CandidateRef(
            chunk_id=unauthorised_chunk_id,
            source_object_id=uuid4(),
            source=Source.GOOGLE_DRIVE,
            fused_score=1.0,
        ),
    )
    rows = [
        (
            chunk_ids[ordinal],
            text,
            ordinal,
            object_version_id,
            "drive-rev-outage-2026-q3",
            "Q3 payment gateway outage postmortem",
            source_object_id,
            Source.GOOGLE_DRIVE.value,
            "https://drive.google.com/document/d/outage",
        )
        for ordinal, text in reversed(
            list(
                enumerate(
                    [
                        "The outage lasted 47 minutes.",
                        "Customer impact: 428 failed checkout attempts.",
                        "Remediation added failover checks.",
                    ]
                )
            )
        )
    ]

    session = _Session(rows)
    evidence = await _load_authorised_text(
        session,  # type: ignore[arg-type]
        candidates,
        {source_object_id},
    )

    assert session.statement is not None
    query_parameters = session.statement.compile().params
    queried_chunk_ids = cast(
        list[UUID],
        next(value for value in query_parameters.values() if isinstance(value, list)),
    )
    assert set(queried_chunk_ids) == set(chunk_ids)
    assert unauthorised_chunk_id not in queried_chunk_ids
    assert [item.chunk_id for item in evidence] == ranked_chunk_ids
    assert [item.fused_score for item in evidence] == scores
    assert [item.chunk_ordinal for item in evidence] == [1, 2, 0]
    assert "428 failed checkout attempts" in evidence[0].text
