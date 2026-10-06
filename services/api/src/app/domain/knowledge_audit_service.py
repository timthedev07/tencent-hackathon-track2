"""Deterministic Knowledge Auditor findings backed by current evidence."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from sqlalchemy import select

from app.auth.oidc import AuthenticatedUser
from app.persistence.models import (
    AclGrant,
    Claim,
    IdentityLink,
    ObjectVersion,
    Principal,
    SourceObject,
    User,
)
from app.persistence.session import get_session_factory, set_tenant_context
from app.retrieval.temporal import active_now


def _finding_id(kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:12]
    return f"finding_{digest}"


async def list_knowledge_findings(user: AuthenticatedUser) -> dict:
    """Return reproducible conflicts, staleness, access risks, and process gaps."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        claim_rows = (
            await session.execute(
                select(Claim, ObjectVersion, SourceObject)
                .join(ObjectVersion, Claim.object_version_id == ObjectVersion.id)
                .join(SourceObject, ObjectVersion.source_object_id == SourceObject.id)
                .where(
                    Claim.tenant_id == user.tenant_id,
                    SourceObject.current_version_id == ObjectVersion.id,
                    active_now(Claim),
                )
            )
        ).all()

        findings: list[dict] = []
        grouped: dict[tuple[str, str], list[tuple[Claim, ObjectVersion, SourceObject]]] = (
            defaultdict(list)
        )
        for claim, version, source_object in claim_rows:
            grouped[(claim.subject, claim.predicate)].append((claim, version, source_object))

        for (subject, predicate), rows in grouped.items():
            values = {claim.object_value for claim, _, _ in rows}
            if len(values) > 1:
                findings.append(
                    {
                        "findingId": _finding_id("conflict", f"{subject}:{predicate}"),
                        "type": "conflict",
                        "severity": "high",
                        "summary": (
                            f"Conflicting {predicate.replace('_', ' ')} values for {subject}: "
                            f"{', '.join(sorted(values))}."
                        ),
                        "evidence": [_evidence(version, obj) for _, version, obj in rows],
                        "recommendedAction": "Assign an owner to reconcile the source records.",
                    }
                )

        for claim, version, source_object in claim_rows:
            if claim.predicate == "freshness" and claim.object_value == "stale":
                findings.append(
                    {
                        "findingId": _finding_id("staleness", str(source_object.id)),
                        "type": "staleness",
                        "severity": "medium",
                        "summary": (
                            f"{version.normalised_title} is marked stale but remains current."
                        ),
                        "evidence": [_evidence(version, source_object)],
                        "recommendedAction": "Review and publish a current runbook revision.",
                    }
                )
            if claim.predicate == "review_status" and "missing" in claim.object_value:
                findings.append(
                    {
                        "findingId": _finding_id("process_gap", str(source_object.id)),
                        "type": "process_gap",
                        "severity": "high",
                        "summary": (
                            f"{claim.subject} closed with {claim.object_value}; required review "
                            "is incomplete."
                        ),
                        "evidence": [_evidence(version, source_object)],
                        "recommendedAction": "Complete and record the required security review.",
                    }
                )

        risk_row = (
            await session.execute(
                select(AclGrant, User, SourceObject, ObjectVersion)
                .join(Principal, AclGrant.principal_id == Principal.id)
                .join(
                    IdentityLink,
                    (IdentityLink.source_connection_id == Principal.source_connection_id)
                    & (IdentityLink.provider_subject_id == Principal.provider_principal_id),
                )
                .join(User, IdentityLink.user_id == User.id)
                .join(SourceObject, AclGrant.source_object_id == SourceObject.id)
                .join(ObjectVersion, SourceObject.current_version_id == ObjectVersion.id)
                .where(
                    AclGrant.tenant_id == user.tenant_id,
                    User.display_name.ilike("%contractor%"),
                    AclGrant.grant_effect == "allow",
                    AclGrant.valid_to.is_(None),
                    active_now(AclGrant),
                    active_now(IdentityLink),
                )
                .limit(1)
            )
        ).first()
        if risk_row is not None:
            grant, contractor, source_object, version = risk_row
            findings.append(
                {
                    "findingId": _finding_id("access_risk", str(grant.id)),
                    "type": "access_risk",
                    "severity": "medium",
                    "summary": (
                        f"Contractor {contractor.display_name} has an unbounded read grant to "
                        f"{version.normalised_title}."
                    ),
                    "evidence": [_evidence(version, source_object)],
                    "recommendedAction": "Add an expiry and owner review to the contractor grant.",
                }
            )

        findings.sort(key=_finding_sort_key)
        counts: dict[str, int] = defaultdict(int)
        for finding in findings:
            counts[str(finding["type"])] += 1
        return {"findings": findings, "counts": dict(counts)}


def _evidence(version: ObjectVersion, source_object: SourceObject) -> dict:
    return {
        "source": source_object.source,
        "sourceObjectId": str(source_object.id),
        "objectVersionId": str(version.id),
        "revision": version.source_revision,
        "contentHash": version.content_hash,
    }


def _finding_sort_key(item: dict[str, object]) -> tuple[str, str]:
    return str(item["type"]), str(item["findingId"])
