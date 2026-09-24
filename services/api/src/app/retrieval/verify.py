"""Output verifier — deterministic validation of generated answers.

Rejects if: evidence ID not in authorised context, claim has no evidence IDs,
answer cites denied item, schema invalid, material claims unmappable.

Repair policy: attempt 1 = structured generation; if invalid → one repair
with SAME authorised context; if still invalid → extractive fallback.
Repair MUST NOT widen retrieval or access new evidence.
"""

from __future__ import annotations

import re

from app.domain.types import AuthorisedEvidence, GeneratedAnswer, GeneratedClaim
from app.observability.logging import get_logger
from app.observability.metrics import CITATION_VERIFICATION_FAILURES

logger = get_logger(__name__)

_EVI_ID_PATTERN = re.compile(r"evi_[0-9a-fA-F]+")
_MIN_CLAIMS_ANSWER_LENGTH = 50


class VerificationResult:
    def __init__(
        self,
        *,
        valid: bool,
        reason: str = "",
        answer: GeneratedAnswer | None = None,
    ) -> None:
        self.valid = valid
        self.reason = reason
        self.answer = answer


def _extract_authorised_ids(evidence: list[AuthorisedEvidence]) -> set[str]:
    return {f"evi_{str(e.evidence_id).replace('-', '')[:12]}" for e in evidence}


def _extract_cited_ids(answer: GeneratedAnswer) -> set[str]:
    cited: set[str] = set()
    for claim in answer.claims:
        for evi_id in claim.evidence_ids:
            cited.add(f"evi_{str(evi_id).replace('-', '')[:12]}")
    cited.update(_EVI_ID_PATTERN.findall(answer.answer))
    return cited


def verify_answer(
    answer: GeneratedAnswer,
    authorised_evidence: list[AuthorisedEvidence],
) -> VerificationResult:
    authorised_ids = _extract_authorised_ids(authorised_evidence)

    if not answer.answer or not answer.answer.strip():
        CITATION_VERIFICATION_FAILURES.labels(reason="empty_answer").inc()
        return VerificationResult(valid=False, reason="empty_answer")

    for claim in answer.claims:
        if not claim.text or not claim.text.strip():
            CITATION_VERIFICATION_FAILURES.labels(reason="empty_claim_text").inc()
            return VerificationResult(valid=False, reason="empty_claim_text")

    cited_ids = _extract_cited_ids(answer)
    unauthorised = cited_ids - authorised_ids
    if unauthorised:
        CITATION_VERIFICATION_FAILURES.labels(reason="unauthorised_citation").inc()
        return VerificationResult(valid=False, reason=f"unauthorised_citations: {len(unauthorised)}")

    for idx, claim in enumerate(answer.claims):
        if not claim.evidence_ids:
            CITATION_VERIFICATION_FAILURES.labels(reason="claim_without_evidence").inc()
            return VerificationResult(valid=False, reason=f"claim_{idx}_without_evidence")

    if not answer.claims and len(answer.answer) > _MIN_CLAIMS_ANSWER_LENGTH:
        CITATION_VERIFICATION_FAILURES.labels(reason="no_claims_for_material_answer").inc()
        return VerificationResult(valid=False, reason="no_claims_for_material_answer")

    return VerificationResult(valid=True, reason="verified", answer=answer)


def build_extractive_fallback(
    evidence: list[AuthorisedEvidence],
    query_text: str,
) -> GeneratedAnswer:
    claims: list[GeneratedClaim] = []
    for ev in evidence[:5]:
        f"evi_{str(ev.evidence_id).replace('-', '')[:12]}"
        text = ev.text.strip()
        if not text:
            continue
        sentences = text.split(". ")
        if sentences:
            sentence = sentences[0] + "."
            claims.append(GeneratedClaim(text=sentence, evidence_ids=[ev.evidence_id]))

    answer_text = "Based on the available evidence:\n\n"
    for claim in claims:
        answer_text += f"- {claim.text}\n"

    if not claims:
        answer_text = "The available evidence is insufficient to answer this question."

    return GeneratedAnswer(answer=answer_text, claims=claims)
