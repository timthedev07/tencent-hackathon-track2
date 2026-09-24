"""Reranking of authorised evidence.

Reranks ONLY authorised evidence — never denied.
Source diversity: no more than SOURCE_DIVERSITY_MAX_PCT from one source.
Source authority and recency NEVER override access.
"""

from __future__ import annotations

from collections import defaultdict

from app.domain.types import AuthorisedEvidence, RerankedEvidence, Source
from app.observability.logging import get_logger
from app.settings import get_settings

logger = get_logger(__name__)

_SOURCE_AUTHORITY: dict[Source, float] = {
    Source.CONFLUENCE: 1.0,
    Source.JIRA: 0.95,
    Source.GOOGLE_DRIVE: 0.85,
    Source.SLACK: 0.70,
}


def _compute_rerank_score(evidence: AuthorisedEvidence, fused_score: float) -> float:
    authority = _SOURCE_AUTHORITY.get(evidence.source, 0.5)
    return 0.70 * fused_score + 0.30 * authority


def apply_source_diversity(
    ranked: list[RerankedEvidence],
    max_pct: float,
    target_count: int,
) -> list[RerankedEvidence]:
    max_per_source = max(1, int(target_count * max_pct))
    source_counts: dict[str, int] = defaultdict(int)
    selected: list[RerankedEvidence] = []
    deferred: list[RerankedEvidence] = []

    for item in ranked:
        source_key = item.evidence.source.value
        if source_counts[source_key] < max_per_source:
            selected.append(item)
            source_counts[source_key] += 1
        else:
            deferred.append(item)

    if len(selected) < target_count and deferred:
        for item in deferred:
            if len(selected) >= target_count:
                break
            selected.append(item)

    for idx, item in enumerate(selected):
        item.rank = idx

    return selected


def rerank_authorised_evidence(
    evidence_list: list[AuthorisedEvidence],
    target_count: int | None = None,
) -> list[RerankedEvidence]:
    settings = get_settings()
    target = target_count or settings.FINAL_EVIDENCE_COUNT

    scored = [
        RerankedEvidence(
            evidence=e,
            rerank_score=_compute_rerank_score(e, e.fused_score),
            rank=idx,
        )
        for idx, e in enumerate(evidence_list)
    ]

    scored.sort(key=lambda r: (-r.rerank_score, str(r.evidence.evidence_id)))

    result = apply_source_diversity(scored, settings.SOURCE_DIVERSITY_MAX_PCT, target)

    logger.info(
        "Reranking complete",
        input_count=len(evidence_list),
        output_count=len(result),
    )
    return result
