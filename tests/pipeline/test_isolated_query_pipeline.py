"""Isolated pipeline tests using provider-shaped mock payloads.

These tests intentionally avoid the UI and database. They exercise the same
query-path module contracts described in ``tencent_hackathon_implementation_spec.md``:
provider payload normalisation, ID-only candidate retrieval, local ACL
projection, live reauthorisation, authorised text loading, reranking, context
compilation, generation verification, citation reauth, and audit hashing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.audit.canonicalise import GENESIS_MARKER, compute_event_hash
from app.domain.types import (
    AccessCheckResult,
    AccessOutcome,
    AuthorisedEvidence,
    GeneratedAnswer,
    GeneratedClaim,
    Source,
)
from app.retrieval.context import compile_context
from app.retrieval.lexical import LexicalHit
from app.retrieval.rerank import rerank_authorised_evidence
from app.retrieval.rrf import fuse_candidates
from app.retrieval.vector import VectorHit
from app.retrieval.verify import build_extractive_fallback, verify_answer

TENANT_ID = uuid5(NAMESPACE_URL, "tenant:acme-corp")
ALICE_USER_ID = uuid5(NAMESPACE_URL, "user:alice")
BOB_USER_ID = uuid5(NAMESPACE_URL, "user:bob")
DAVE_USER_ID = uuid5(NAMESPACE_URL, "user:dave")

RESTRICTED_STRATEGY_TEXT = "Project Falcon acquisition target"
RESTRICTED_FINANCE_TEXT = "Budget allocation is 60% engineering, 25% sales, 15% operations"
SLACK_DEPLOYMENT_TEXT = "All smoke tests passed and the OIDC validation is live"


@dataclass(frozen=True)
class ProviderIdentity:
    provider_subject_by_source: dict[Source, str]
    group_memberships_by_source: dict[Source, set[str]]


@dataclass(frozen=True)
class NormalisedProviderObject:
    source: Source
    external_object_id: str
    object_type: str
    parent_external_id: str | None
    canonical_url: str
    title: str
    source_revision: str
    text: str
    provider_metadata: dict[str, Any]


@dataclass(frozen=True)
class ProviderDocument:
    source_object_id: UUID
    chunk_id: UUID
    evidence_id: UUID
    provider_payload: dict[str, Any]
    acl_payload: dict[str, Any]
    normalised: NormalisedProviderObject
    chunk_ordinal: int = 0


@dataclass
class PipelineTrace:
    candidate_refs: list[object] = field(default_factory=list)
    local_acl_results: dict[UUID, AccessOutcome] = field(default_factory=dict)
    live_checked_object_ids: list[UUID] = field(default_factory=list)
    live_access_results: dict[UUID, AccessOutcome] = field(default_factory=dict)
    authorised_evidence: list[AuthorisedEvidence] = field(default_factory=list)
    reranker_input_texts: list[str] = field(default_factory=list)
    context_texts: list[str] = field(default_factory=list)
    generated_answer: str = ""
    service_account_used: bool = False
    statuses: list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    status: str
    answer: GeneratedAnswer | None
    evidence: list[AuthorisedEvidence]
    access_results: dict[UUID, AccessCheckResult]
    partial_sources: list[str]
    audit_event_hash: str | None
    trace: PipelineTrace


@dataclass
class FakeAuditEvent:
    event_type: str
    sequence_no: int
    tenant_id: UUID
    query_id: UUID | None
    canonical_payload: dict[str, Any]
    created_at: datetime


def _id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tencent-hackathon:{name}")


def _provider_payloads() -> dict[str, dict[str, Any]]:
    """Mock responses shaped like the provider APIs used by the connectors."""
    return {
        "confluence_public_page": {
            "id": "confluence-public-page-001",
            "status": "current",
            "title": "Deployment Runbook",
            "spaceId": "ENG",
            "parentId": "space:ENG",
            "version": {
                "number": 7,
                "createdAt": "2026-09-23T10:00:00Z",
                "authorId": "confluence-user:alice-acme",
            },
            "body": {
                "storage": {
                    "representation": "storage",
                    "value": (
                        "<h1>Deployment Runbook</h1><h2>Steps</h2>"
                        "<p>Build the Docker image, deploy to staging, run smoke tests, "
                        "then deploy to production.</p><h2>Rollback</h2>"
                        "<p>Use the rollback script if production validation fails.</p>"
                    ),
                }
            },
            "_links": {
                "base": "https://company.atlassian.net/wiki",
                "webui": "/spaces/ENG/pages/001",
            },
        },
        "confluence_public_page_v2": {
            "id": "confluence-public-page-001",
            "status": "current",
            "title": "Deployment Runbook",
            "spaceId": "ENG",
            "parentId": "space:ENG",
            "version": {
                "number": 8,
                "createdAt": "2026-09-24T09:00:00Z",
                "authorId": "confluence-user:alice-acme",
            },
            "body": {
                "storage": {
                    "representation": "storage",
                    "value": (
                        "<h1>Deployment Runbook</h1><h2>Rollback</h2>"
                        "<p>Rollback now uses a canary validation step before reverting "
                        "traffic to the previous release.</p>"
                    ),
                }
            },
            "_links": {
                "base": "https://company.atlassian.net/wiki",
                "webui": "/spaces/ENG/pages/001",
            },
        },
        "confluence_restricted_page": {
            "id": "confluence-restricted-page-002",
            "status": "current",
            "title": "Q4 Strategy Roadmap (Confidential)",
            "spaceId": "EXEC",
            "parentId": "space:EXEC",
            "version": {
                "number": 4,
                "createdAt": "2026-09-22T14:00:00Z",
                "authorId": "confluence-user:carol-acme",
            },
            "body": {
                "storage": {
                    "representation": "storage",
                    "value": (
                        "<h1>Q4 Strategy Roadmap</h1><p>Confidential planning: "
                        f"{RESTRICTED_STRATEGY_TEXT}. Product pivot to enterprise segment.</p>"
                    ),
                }
            },
            "_links": {
                "base": "https://company.atlassian.net/wiki",
                "webui": "/spaces/EXEC/pages/002",
            },
        },
        "jira_shared_issue": {
            "expand": "renderedFields,names,schema,operations,editmeta,changelog",
            "id": "10001",
            "self": "https://company.atlassian.net/rest/api/3/issue/10001",
            "key": "PROJ-123",
            "fields": {
                "project": {"id": "10100", "key": "PROJ", "name": "Platform"},
                "issuetype": {"id": "10002", "name": "Task"},
                "summary": "Implement authentication middleware",
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Validate OIDC tokens on every request and resolve tenant "
                                        "identity server-side."
                                    ),
                                }
                            ],
                        }
                    ],
                },
                "comment": {
                    "comments": [
                        {
                            "id": "20001",
                            "author": {"accountId": "jira-user:alice-acme"},
                            "created": "2026-09-20T09:00:00.000+0000",
                            "body": {
                                "type": "doc",
                                "version": 1,
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {"type": "text", "text": "I'll start on this today."}
                                        ],
                                    }
                                ],
                            },
                        },
                        {
                            "id": "20002",
                            "author": {"accountId": "jira-user:bob-acme"},
                            "created": "2026-09-20T10:00:00.000+0000",
                            "body": {
                                "type": "doc",
                                "version": 1,
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "Handle token expiry gracefully.",
                                            }
                                        ],
                                    }
                                ],
                            },
                        },
                    ]
                },
                "security": None,
                "updated": "2026-09-20T10:00:00.000+0000",
            },
        },
        "slack_public_thread": {
            "ok": True,
            "channel": {
                "id": "C1234567890",
                "name": "eng-deployment",
                "is_channel": True,
                "is_private": False,
                "is_archived": False,
            },
            "messages": [
                {
                    "type": "message",
                    "user": "U-alice-001",
                    "text": f"The deployment went smoothly today. {SLACK_DEPLOYMENT_TEXT}.",
                    "ts": "1234567890.001001",
                    "thread_ts": "1234567890.001001",
                    "team": "TACME",
                    "blocks": [
                        {
                            "type": "rich_text",
                            "block_id": "b1",
                            "elements": [
                                {
                                    "type": "rich_text_section",
                                    "elements": [
                                        {
                                            "type": "text",
                                            "text": (
                                                "The deployment went smoothly today. "
                                                f"{SLACK_DEPLOYMENT_TEXT}."
                                            ),
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
            "response_metadata": {"next_cursor": ""},
        },
        "google_drive_shared_doc": {
            "file": {
                "kind": "drive#file",
                "id": "1a2b3c4d5e6f7g8h9i0j",
                "name": "Team Handbook",
                "mimeType": "application/vnd.google-apps.document",
                "webViewLink": "https://drive.google.com/file/d/1a2b3c4d5e6f7g8h9i0j",
                "parents": ["folder:team-docs"],
                "headRevisionId": "drive-rev-2026-09-20T08:00:00Z",
                "modifiedTime": "2026-09-20T08:00:00.000Z",
                "owners": [{"kind": "drive#user", "permissionId": "google-user:alice-acme-001"}],
            },
            "exportedText": (
                "# Team Handbook\n\nWorking Hours: Core hours are 10:00-16:00. "
                "Deployment Process: See the Confluence runbook. On-Call Rotation is weekly."
            ),
        },
        "google_drive_restricted_doc": {
            "file": {
                "kind": "drive#file",
                "id": "0x9y8z7w6v5u4t3s2r1",
                "name": "Confidential Financial Projections",
                "mimeType": "application/vnd.google-apps.document",
                "webViewLink": "https://drive.google.com/file/d/0x9y8z7w6v5u4t3s2r1",
                "parents": ["folder:exec-confidential"],
                "headRevisionId": "drive-rev-2026-09-21T12:00:00Z",
                "modifiedTime": "2026-09-21T12:00:00.000Z",
                "owners": [{"kind": "drive#user", "permissionId": "google-user:cfo-acme-001"}],
            },
            "exportedText": (
                "# Financial Projections Q4\n\nRevenue forecast is confidential. "
                f"{RESTRICTED_FINANCE_TEXT}."
            ),
        },
    }


def _acl_payloads() -> dict[str, dict[str, Any]]:
    return {
        "confluence_public_page": {
            "operation": "read",
            "restrictions": {
                "user": {"results": [], "size": 0},
                "group": {"results": [{"type": "group", "id": "eng-team", "name": "eng-team"}], "size": 1},
            },
        },
        "confluence_public_page_v2": {
            "operation": "read",
            "restrictions": {
                "user": {"results": [], "size": 0},
                "group": {"results": [{"type": "group", "id": "eng-team", "name": "eng-team"}], "size": 1},
            },
        },
        "confluence_restricted_page": {
            "operation": "read",
            "restrictions": {
                "user": {"results": [], "size": 0},
                "group": {
                    "results": [{"type": "group", "id": "exec-leadership", "name": "exec-leadership"}],
                    "size": 1,
                },
            },
        },
        "jira_shared_issue": {
            "permissions": {
                "BROWSE_PROJECTS": {
                    "id": "10",
                    "key": "BROWSE_PROJECTS",
                    "name": "Browse Projects",
                    "type": "PROJECT",
                    "havePermission": True,
                }
            },
            "projectRoleActors": [{"type": "atlassian-group-role-actor", "name": "eng-team"}],
        },
        "slack_public_thread": {
            "ok": True,
            "channel": {"id": "C1234567890", "is_channel": True, "is_private": False},
            "members": ["U-alice-001", "U-bob-002"],
        },
        "google_drive_shared_doc": {
            "kind": "drive#permissionList",
            "permissions": [
                {
                    "kind": "drive#permission",
                    "id": "perm-eng",
                    "type": "group",
                    "role": "reader",
                    "emailAddress": "eng-team@company.com",
                }
            ],
        },
        "google_drive_restricted_doc": {
            "kind": "drive#permissionList",
            "permissions": [
                {
                    "kind": "drive#permission",
                    "id": "perm-cfo",
                    "type": "user",
                    "role": "reader",
                    "emailAddress": "cfo@company.com",
                },
                {
                    "kind": "drive#permission",
                    "id": "perm-ceo",
                    "type": "user",
                    "role": "reader",
                    "emailAddress": "ceo@company.com",
                },
            ],
        },
    }


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _flatten_adf(node: Any) -> list[str]:
    if isinstance(node, dict):
        pieces: list[str] = []
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            pieces.append(node["text"])
        for value in node.values():
            pieces.extend(_flatten_adf(value))
        return pieces
    if isinstance(node, list):
        pieces = []
        for item in node:
            pieces.extend(_flatten_adf(item))
        return pieces
    return []


def _normalise_provider_payload(source: Source, payload: dict[str, Any]) -> NormalisedProviderObject:
    if source == Source.CONFLUENCE:
        base = payload["_links"]["base"]
        webui = payload["_links"]["webui"]
        return NormalisedProviderObject(
            source=source,
            external_object_id=payload["id"],
            object_type="page",
            parent_external_id=payload["parentId"],
            canonical_url=f"{base}{webui}",
            title=payload["title"],
            source_revision=payload["version"]["createdAt"],
            text=_strip_html(payload["body"]["storage"]["value"]),
            provider_metadata={"spaceId": payload["spaceId"], "versionNumber": payload["version"]["number"]},
        )
    if source == Source.JIRA:
        fields = payload["fields"]
        comments = [" ".join(_flatten_adf(comment["body"])) for comment in fields["comment"]["comments"]]
        text = "\n".join([fields["summary"], " ".join(_flatten_adf(fields["description"])), *comments])
        return NormalisedProviderObject(
            source=source,
            external_object_id=payload["key"],
            object_type=fields["issuetype"]["name"].lower(),
            parent_external_id=f"project:{fields['project']['key']}",
            canonical_url=f"https://company.atlassian.net/jira/{payload['key']}",
            title=f"{payload['key']}: {fields['summary']}",
            source_revision=fields["updated"],
            text=text,
            provider_metadata={"issueId": payload["id"], "projectKey": fields["project"]["key"]},
        )
    if source == Source.SLACK:
        channel = payload["channel"]
        first_message = payload["messages"][0]
        text = "\n".join(message["text"] for message in payload["messages"])
        return NormalisedProviderObject(
            source=source,
            external_object_id=f"{channel['id']}-{first_message['thread_ts']}",
            object_type="message",
            parent_external_id=f"channel:{channel['id']}",
            canonical_url=(
                f"https://company.slack.com/archives/{channel['id']}/"
                f"p{first_message['ts'].replace('.', '')}"
            ),
            title=f"#{channel['name']} thread",
            source_revision=first_message["ts"],
            text=text,
            provider_metadata={"isPrivate": channel["is_private"], "team": first_message["team"]},
        )
    file_payload = payload["file"]
    return NormalisedProviderObject(
        source=source,
        external_object_id=file_payload["id"],
        object_type="document",
        parent_external_id=file_payload["parents"][0],
        canonical_url=file_payload["webViewLink"],
        title=file_payload["name"],
        source_revision=file_payload["headRevisionId"],
        text=payload["exportedText"],
        provider_metadata={"mimeType": file_payload["mimeType"], "modifiedTime": file_payload["modifiedTime"]},
    )


def _provider_documents(use_updated_confluence: bool = False) -> list[ProviderDocument]:
    payloads = _provider_payloads()
    acls = _acl_payloads()
    keys_and_sources = [
        ("confluence_public_page_v2" if use_updated_confluence else "confluence_public_page", Source.CONFLUENCE),
        ("confluence_restricted_page", Source.CONFLUENCE),
        ("jira_shared_issue", Source.JIRA),
        ("slack_public_thread", Source.SLACK),
        ("google_drive_shared_doc", Source.GOOGLE_DRIVE),
        ("google_drive_restricted_doc", Source.GOOGLE_DRIVE),
    ]
    docs: list[ProviderDocument] = []
    for key, source in keys_and_sources:
        source_object_key = "confluence_public_page" if key == "confluence_public_page_v2" else key
        docs.append(
            ProviderDocument(
                source_object_id=_id(f"object:{source_object_key}"),
                chunk_id=_id(f"chunk:{source_object_key}"),
                evidence_id=_id(f"evidence:{source_object_key}"),
                provider_payload=payloads[key],
                acl_payload=acls[key],
                normalised=_normalise_provider_payload(source, payloads[key]),
            )
        )
    return docs


def _google_drive_outage_documents() -> list[ProviderDocument]:
    base = next(
        doc
        for doc in _provider_documents()
        if doc.normalised.external_object_id == "1a2b3c4d5e6f7g8h9i0j"
    )
    source_object_id = _id("object:google_drive_outage_postmortem")
    paragraphs = [
        "The Q3 payment gateway outage began at 14:03 and lasted 47 minutes.",
        "Customer impact: 428 failed checkout attempts.",
        "Payment gateway remediation added failover checks and a follow-up review.",
    ]
    return [
        ProviderDocument(
            source_object_id=source_object_id,
            chunk_id=_id(f"chunk:google_drive_outage_postmortem:{ordinal}"),
            evidence_id=_id(f"evidence:google_drive_outage_postmortem:{ordinal}"),
            provider_payload=base.provider_payload,
            acl_payload=base.acl_payload,
            normalised=replace(
                base.normalised,
                external_object_id="google-drive-outage-postmortem",
                title="Q3 payment gateway outage postmortem",
                source_revision="drive-rev-outage-2026-q3",
                text=text,
            ),
            chunk_ordinal=ordinal,
        )
        for ordinal, text in enumerate(paragraphs)
    ]


def _identity_for(user_id: UUID) -> ProviderIdentity:
    if user_id == ALICE_USER_ID:
        return ProviderIdentity(
            provider_subject_by_source={
                Source.CONFLUENCE: "confluence-user:alice-acme",
                Source.JIRA: "jira-user:alice-acme",
                Source.SLACK: "U-alice-001",
                Source.GOOGLE_DRIVE: "alice.chen@company.com",
            },
            group_memberships_by_source={
                Source.CONFLUENCE: {"eng-team"},
                Source.JIRA: {"eng-team"},
                Source.SLACK: {"C1234567890"},
                Source.GOOGLE_DRIVE: {"eng-team@company.com"},
            },
        )
    if user_id == BOB_USER_ID:
        return ProviderIdentity(
            provider_subject_by_source={
                Source.CONFLUENCE: "confluence-user:bob-acme",
                Source.JIRA: "jira-user:bob-acme",
                Source.SLACK: "U-bob-002",
                Source.GOOGLE_DRIVE: "bob.martinez@company.com",
            },
            group_memberships_by_source={
                Source.CONFLUENCE: {"eng-team"},
                Source.JIRA: {"eng-team"},
                Source.SLACK: {"C1234567890"},
                Source.GOOGLE_DRIVE: {"eng-team@company.com"},
            },
        )
    return ProviderIdentity(provider_subject_by_source={}, group_memberships_by_source={})


_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "does",
    "is",
    "me",
    "on",
    "say",
    "the",
    "to",
    "what",
    "who",
}


def _terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", value.lower())
        if term not in _STOPWORDS and len(term) > 1
    }


class InMemoryPipelineHarness:
    def __init__(
        self,
        docs: list[ProviderDocument],
        *,
        revoked_object_ids: set[UUID] | None = None,
        timed_out_sources: set[Source] | None = None,
    ) -> None:
        self.docs = docs
        self.by_object_id = {doc.source_object_id: doc for doc in docs}
        self.by_chunk_id = {doc.chunk_id: doc for doc in docs}
        self.by_evidence_id: dict[UUID, AuthorisedEvidence] = {}
        self.revoked_object_ids = revoked_object_ids or set()
        self.timed_out_sources = timed_out_sources or set()
        self.previous_audit_hash = GENESIS_MARKER
        self.audit_sequence = 0

    def run(self, query_text: str, user_id: UUID) -> PipelineResult:
        trace = PipelineTrace(statuses=["created", "authenticated"])
        vector_hits, lexical_hits = self._candidate_retrieval(query_text)
        candidates = fuse_candidates(vector_hits, lexical_hits, k=60)
        trace.candidate_refs.extend(candidates)
        trace.statuses.append("candidates")

        local_verdicts = self._local_acl_projection(user_id, candidates)
        trace.local_acl_results = {obj_id: verdict.outcome for obj_id, verdict in local_verdicts.items()}
        candidates_after_local = [
            candidate
            for candidate in candidates
            if local_verdicts[candidate.source_object_id].outcome != AccessOutcome.DENY
        ]

        access_results: dict[UUID, AccessCheckResult] = {}
        ordered_unique_object_ids: list[UUID] = []
        for candidate in candidates_after_local:
            if candidate.source_object_id not in ordered_unique_object_ids:
                ordered_unique_object_ids.append(candidate.source_object_id)
        for object_id in ordered_unique_object_ids:
            doc = self.by_object_id[object_id]
            trace.live_checked_object_ids.append(object_id)
            access_results[object_id] = self._live_reauthorise(user_id, doc)
        trace.live_access_results = {obj_id: result.outcome for obj_id, result in access_results.items()}

        authorised_ids = {
            object_id for object_id, result in access_results.items() if result.outcome == AccessOutcome.ALLOW
        }
        partial_sources = sorted(
            {
                self.by_object_id[object_id].normalised.source.value
                for object_id, result in access_results.items()
                if result.outcome == AccessOutcome.TIMEOUT
            }
        )
        if not authorised_ids:
            trace.statuses.append("denied")
            return PipelineResult(
                status="denied",
                answer=None,
                evidence=[],
                access_results=access_results,
                partial_sources=partial_sources,
                audit_event_hash=None,
                trace=trace,
            )

        evidence = self._load_authorised_text(candidates_after_local, authorised_ids)
        trace.authorised_evidence = evidence
        trace.statuses.append("reauthorised")
        if not evidence:
            trace.statuses.append("denied")
            return PipelineResult(
                status="denied",
                answer=None,
                evidence=[],
                access_results=access_results,
                partial_sources=partial_sources,
                audit_event_hash=None,
                trace=trace,
            )

        trace.reranker_input_texts = [item.text for item in evidence]
        reranked = rerank_authorised_evidence(evidence, target_count=10)
        trace.statuses.append("reranked")

        context_evidence = [item.evidence for item in reranked]
        compiled_context = compile_context(context_evidence)
        trace.context_texts = [item.text for item in compiled_context.evidence_blocks]
        trace.statuses.append("compiled")

        generated = self._generate(compiled_context)
        trace.generated_answer = generated.answer
        trace.statuses.append("generated")

        verification = verify_answer(generated, compiled_context.evidence_blocks)
        final_answer = verification.answer if verification.valid else build_extractive_fallback(
            compiled_context.evidence_blocks, query_text
        )
        trace.statuses.append("verified")

        final_status = "partial" if partial_sources else "completed"
        audit_hash = self._audit_commit(
            query_id=_id(f"query:{query_text}:{user_id}"),
            status=final_status,
            evidence=compiled_context.evidence_blocks,
            access_results=access_results,
            partial_sources=partial_sources,
        )
        trace.statuses.append(final_status)
        return PipelineResult(
            status=final_status,
            answer=final_answer,
            evidence=compiled_context.evidence_blocks,
            access_results=access_results,
            partial_sources=partial_sources,
            audit_event_hash=audit_hash,
            trace=trace,
        )

    def open_citation(self, evidence_id: UUID, user_id: UUID) -> dict[str, Any]:
        evidence = self.by_evidence_id[evidence_id]
        doc = self.by_object_id[evidence.source_object_id]
        access = self._live_reauthorise(user_id, doc)
        if access.outcome != AccessOutcome.ALLOW:
            return {"status": "access_revoked", "reason": access.reason_code}
        return {"status": "ok", "quotedSpan": evidence.text, "title": evidence.title}

    def _candidate_retrieval(self, query_text: str) -> tuple[list[VectorHit], list[LexicalHit]]:
        query_terms = _terms(query_text)
        scored: list[tuple[int, ProviderDocument]] = []
        for doc in self.docs:
            score = len(query_terms & _terms(f"{doc.normalised.title} {doc.normalised.text}"))
            if score:
                scored.append((score, doc))
        scored.sort(key=lambda item: (-item[0], item[1].normalised.external_object_id))
        vector_hits = [
            VectorHit(
                chunk_id=doc.chunk_id,
                source_object_id=doc.source_object_id,
                source=doc.normalised.source,
                score=score / 10,
            )
            for score, doc in scored
        ]
        lexical_hits = [
            LexicalHit(
                chunk_id=doc.chunk_id,
                source_object_id=doc.source_object_id,
                source=doc.normalised.source,
                rank=rank,
            )
            for rank, (_, doc) in enumerate(scored)
        ]
        return vector_hits, lexical_hits

    def _local_acl_projection(self, user_id: UUID, candidates: list[Any]) -> dict[UUID, AccessCheckResult]:
        seen: set[UUID] = set()
        verdicts: dict[UUID, AccessCheckResult] = {}
        for candidate in candidates:
            if candidate.source_object_id in seen:
                continue
            seen.add(candidate.source_object_id)
            doc = self.by_object_id[candidate.source_object_id]
            verdicts[candidate.source_object_id] = self._provider_acl_result(user_id, doc, local=True)
        return verdicts

    def _live_reauthorise(self, user_id: UUID, doc: ProviderDocument) -> AccessCheckResult:
        if doc.normalised.source in self.timed_out_sources:
            return AccessCheckResult(
                outcome=AccessOutcome.TIMEOUT,
                reason_code="provider_timeout",
                provider_request_id=f"req-{doc.normalised.source.value}-timeout",
                latency_ms=1500,
            )
        return self._provider_acl_result(user_id, doc, local=False)

    def _provider_acl_result(
        self, user_id: UUID, doc: ProviderDocument, *, local: bool
    ) -> AccessCheckResult:
        identity = _identity_for(user_id)
        subject = identity.provider_subject_by_source.get(doc.normalised.source)
        if subject is None:
            return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="identity_missing")
        if doc.source_object_id in self.revoked_object_ids:
            return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="access_revoked")

        allowed_subjects, allowed_groups = self._allowed_principals(doc)
        groups = identity.group_memberships_by_source.get(doc.normalised.source, set())
        if subject in allowed_subjects or groups & allowed_groups:
            return AccessCheckResult(
                outcome=AccessOutcome.ALLOW,
                reason_code="local_acl_allow" if local else "live_provider_allow",
                provider_request_id=None if local else f"req-{doc.normalised.external_object_id}",
                latency_ms=None if local else 12,
            )
        return AccessCheckResult(
            outcome=AccessOutcome.DENY,
            reason_code="local_acl_deny" if local else "live_provider_deny",
            provider_request_id=None if local else f"req-{doc.normalised.external_object_id}",
            latency_ms=None if local else 12,
        )

    def _allowed_principals(self, doc: ProviderDocument) -> tuple[set[str], set[str]]:
        source = doc.normalised.source
        acl = doc.acl_payload
        if source == Source.CONFLUENCE:
            restrictions = acl["restrictions"]
            users = {item["accountId"] for item in restrictions["user"]["results"] if "accountId" in item}
            groups = {item["id"] for item in restrictions["group"]["results"]}
            return users, groups
        if source == Source.JIRA:
            groups = {actor["name"] for actor in acl["projectRoleActors"]}
            return set(), groups
        if source == Source.SLACK:
            return set(acl["members"]), {acl["channel"]["id"]}
        users: set[str] = set()
        groups: set[str] = set()
        for permission in acl["permissions"]:
            if permission["role"] not in {"reader", "writer", "owner"}:
                continue
            if permission["type"] == "user":
                users.add(permission["emailAddress"])
            elif permission["type"] == "group":
                groups.add(permission["emailAddress"])
        return users, groups

    def _load_authorised_text(
        self, candidates: list[Any], authorised_object_ids: set[UUID]
    ) -> list[AuthorisedEvidence]:
        evidence: list[AuthorisedEvidence] = []
        seen_chunks: set[UUID] = set()
        for candidate in candidates:
            object_id = candidate.source_object_id
            if object_id not in authorised_object_ids or candidate.chunk_id in seen_chunks:
                continue
            seen_chunks.add(candidate.chunk_id)
            doc = self.by_chunk_id[candidate.chunk_id]
            ev = AuthorisedEvidence(
                evidence_id=doc.evidence_id,
                chunk_id=doc.chunk_id,
                source_object_id=object_id,
                source=doc.normalised.source,
                source_revision=doc.normalised.source_revision,
                title=doc.normalised.title,
                canonical_url=doc.normalised.canonical_url,
                text=doc.normalised.text,
                fused_score=candidate.fused_score,
                chunk_ordinal=doc.chunk_ordinal,
            )
            evidence.append(ev)
            self.by_evidence_id[ev.evidence_id] = ev
        return evidence

    def _generate(self, context: Any) -> GeneratedAnswer:
        claims: list[GeneratedClaim] = []
        answer_lines: list[str] = []
        for ev in context.evidence_blocks:
            first_sentence = ev.text.split(". ")[0].strip()
            if not first_sentence.endswith("."):
                first_sentence += "."
            evi_ref = f"evi_{str(ev.evidence_id).replace('-', '')[:12]}"
            answer_lines.append(f"{first_sentence} [{evi_ref}]")
            claims.append(GeneratedClaim(text=first_sentence, evidence_ids=[ev.evidence_id]))
        return GeneratedAnswer(answer="\n".join(answer_lines), claims=claims)

    def _audit_commit(
        self,
        *,
        query_id: UUID,
        status: str,
        evidence: list[AuthorisedEvidence],
        access_results: dict[UUID, AccessCheckResult],
        partial_sources: list[str],
    ) -> str:
        self.audit_sequence += 1
        event = FakeAuditEvent(
            event_type="query.completed",
            sequence_no=self.audit_sequence,
            tenant_id=TENANT_ID,
            query_id=query_id,
            canonical_payload={
                "query_id": str(query_id),
                "status": status,
                "evidence_count": len(evidence),
                "evidence_revisions": [ev.source_revision for ev in evidence],
                "access_decisions": {
                    str(object_id): result.outcome.value for object_id, result in access_results.items()
                },
                "partial_sources": partial_sources,
            },
            created_at=datetime(2026, 10, 6, tzinfo=UTC),
        )
        event_hash = compute_event_hash(event, self.previous_audit_hash)
        self.previous_audit_hash = event_hash
        return event_hash


def _texts(values: list[str]) -> str:
    return "\n".join(values)


def test_provider_mock_payloads_match_provider_api_response_shapes() -> None:
    payloads = _provider_payloads()
    acls = _acl_payloads()

    confluence = payloads["confluence_public_page"]
    assert confluence["body"]["storage"]["representation"] == "storage"
    assert confluence["version"]["number"] == 7
    assert acls["confluence_public_page"]["restrictions"]["group"]["results"][0]["id"] == "eng-team"

    jira = payloads["jira_shared_issue"]
    assert jira["fields"]["description"]["type"] == "doc"
    assert jira["fields"]["comment"]["comments"][0]["body"]["version"] == 1
    assert acls["jira_shared_issue"]["permissions"]["BROWSE_PROJECTS"]["havePermission"] is True

    slack = payloads["slack_public_thread"]
    assert slack["ok"] is True
    assert slack["messages"][0]["thread_ts"] == "1234567890.001001"
    assert acls["slack_public_thread"]["channel"]["is_private"] is False

    drive = payloads["google_drive_shared_doc"]
    assert drive["file"]["kind"] == "drive#file"
    assert drive["file"]["headRevisionId"] == "drive-rev-2026-09-20T08:00:00Z"
    assert acls["google_drive_shared_doc"]["kind"] == "drive#permissionList"


def test_provider_payloads_normalise_into_pipeline_document_shape() -> None:
    docs = {doc.normalised.external_object_id: doc.normalised for doc in _provider_documents()}

    confluence = docs["confluence-public-page-001"]
    assert confluence.source == Source.CONFLUENCE
    assert confluence.object_type == "page"
    assert confluence.parent_external_id == "space:ENG"
    assert "Build the Docker image" in confluence.text

    jira = docs["PROJ-123"]
    assert jira.source == Source.JIRA
    assert jira.parent_external_id == "project:PROJ"
    assert "Handle token expiry gracefully" in jira.text

    slack = docs["C1234567890-1234567890.001001"]
    assert slack.source == Source.SLACK
    assert slack.provider_metadata["isPrivate"] is False
    assert SLACK_DEPLOYMENT_TEXT in slack.text

    drive = docs["1a2b3c4d5e6f7g8h9i0j"]
    assert drive.source == Source.GOOGLE_DRIVE
    assert drive.provider_metadata["mimeType"] == "application/vnd.google-apps.document"
    assert "Core hours are 10:00-16:00" in drive.text


def test_candidate_retrieval_outputs_ids_only_even_when_restricted_docs_match() -> None:
    harness = InMemoryPipelineHarness(_provider_documents())
    vector_hits, lexical_hits = harness._candidate_retrieval(
        "Q4 acquisition budget allocation confidential strategy"
    )
    candidates = fuse_candidates(vector_hits, lexical_hits, k=60)

    assert vector_hits
    assert lexical_hits
    assert candidates
    assert "text" not in VectorHit.model_fields
    assert "text" not in LexicalHit.model_fields
    assert "text" not in type(candidates[0]).model_fields
    assert any(
        harness.by_object_id[candidate.source_object_id].normalised.external_object_id
        == "confluence-restricted-page-002"
        for candidate in candidates
    )


def test_authorised_cross_source_pipeline_generates_verified_answer_after_audit() -> None:
    result = InMemoryPipelineHarness(_provider_documents()).run(
        "What is the deployment process and who is working on authentication middleware?",
        ALICE_USER_ID,
    )

    assert result.status == "completed"
    assert result.answer is not None
    assert result.audit_event_hash is not None and re.fullmatch(r"[0-9a-f]{64}", result.audit_event_hash)
    assert result.trace.statuses[-2:] == ["verified", "completed"]
    assert {ev.source for ev in result.evidence} >= {Source.CONFLUENCE, Source.JIRA}
    assert verify_answer(result.answer, result.evidence).valid
    assert all(outcome == AccessOutcome.ALLOW for outcome in result.trace.live_access_results.values())


def test_google_drive_outage_answer_preserves_and_cites_impact_chunk() -> None:
    documents = _google_drive_outage_documents()
    query = "How many checkout attempts failed during the Q3 payment gateway outage?"

    result = InMemoryPipelineHarness(documents).run(query, ALICE_USER_ID)

    assert result.status == "completed"
    assert result.answer is not None
    assert "428" in result.answer.answer
    assert {item.chunk_ordinal for item in result.trace.authorised_evidence} == {0, 1, 2}
    assert [item.chunk_ordinal for item in result.evidence] == [0, 1, 2]
    impact = next(item for item in result.evidence if "428 failed checkout attempts" in item.text)
    impact_claim = next(claim for claim in result.answer.claims if "428" in claim.text)
    assert impact_claim.evidence_ids == [impact.evidence_id]
    assert impact.source == Source.GOOGLE_DRIVE
    assert impact.source_object_id == _id("object:google_drive_outage_postmortem")

    denied = InMemoryPipelineHarness(documents).run(query, DAVE_USER_ID)
    assert denied.status == "denied"
    assert denied.evidence == []
    assert denied.answer is None


def test_restricted_provider_text_never_reaches_reranker_context_or_answer() -> None:
    result = InMemoryPipelineHarness(_provider_documents()).run(
        "What are the Q4 acquisition target and budget allocation plans?",
        BOB_USER_ID,
    )

    downstream_text = _texts(
        [
            *result.trace.reranker_input_texts,
            *result.trace.context_texts,
            result.trace.generated_answer,
        ]
    )
    assert any(outcome == AccessOutcome.DENY for outcome in result.trace.local_acl_results.values())
    assert result.status == "denied"
    assert RESTRICTED_STRATEGY_TEXT not in downstream_text
    assert RESTRICTED_FINANCE_TEXT not in downstream_text
    assert result.evidence == []
    assert result.answer is None


def test_provider_timeout_returns_partial_and_excludes_timed_out_source_text() -> None:
    result = InMemoryPipelineHarness(
        _provider_documents(), timed_out_sources={Source.SLACK}
    ).run(
        "What happened with the deployment, smoke tests, and team handbook hours?",
        ALICE_USER_ID,
    )

    downstream_text = _texts([*result.trace.context_texts, result.trace.generated_answer])
    assert result.status == "partial"
    assert result.partial_sources == ["slack"]
    assert any(access.outcome == AccessOutcome.TIMEOUT for access in result.access_results.values())
    assert Source.SLACK not in {ev.source for ev in result.evidence}
    assert SLACK_DEPLOYMENT_TEXT not in downstream_text
    assert "Core hours are 10:00-16:00" in downstream_text


def test_missing_identity_denies_without_service_account_fallback() -> None:
    result = InMemoryPipelineHarness(_provider_documents()).run(
        "What is the deployment process?", DAVE_USER_ID
    )

    assert result.status == "denied"
    assert result.trace.service_account_used is False
    assert result.evidence == []
    assert set(result.trace.local_acl_results.values()) == {AccessOutcome.DENY}
    assert result.trace.live_checked_object_ids == []


def test_revocation_applies_on_next_query_and_citation_open_reauthorises() -> None:
    docs = _provider_documents()
    drive_doc_id = _id("object:google_drive_shared_doc")
    before_revocation = InMemoryPipelineHarness(docs)
    first = before_revocation.run(
        "What does the team handbook say about working hours and on-call rotation?",
        ALICE_USER_ID,
    )
    assert first.status == "completed"
    handbook_evidence = next(ev for ev in first.evidence if ev.source_object_id == drive_doc_id)

    before_revocation.revoked_object_ids.add(drive_doc_id)
    citation = before_revocation.open_citation(handbook_evidence.evidence_id, ALICE_USER_ID)
    assert citation == {"status": "access_revoked", "reason": "access_revoked"}

    after_revocation = InMemoryPipelineHarness(docs, revoked_object_ids={drive_doc_id}).run(
        "What does the team handbook say about working hours and on-call rotation?",
        ALICE_USER_ID,
    )
    assert after_revocation.status == "denied"
    assert after_revocation.evidence == []
    assert after_revocation.answer is None


def test_fresh_data_update_uses_latest_revision_while_old_revision_is_retained() -> None:
    before = InMemoryPipelineHarness(_provider_documents(use_updated_confluence=False)).run(
        "What is the rollback procedure in the deployment runbook?",
        ALICE_USER_ID,
    )
    after_docs = _provider_documents(use_updated_confluence=True)
    after = InMemoryPipelineHarness(after_docs).run(
        "What is the rollback procedure in the deployment runbook?",
        ALICE_USER_ID,
    )

    before_runbook = next(ev for ev in before.evidence if ev.source == Source.CONFLUENCE)
    after_runbook = next(ev for ev in after.evidence if ev.source == Source.CONFLUENCE)
    assert before_runbook.source_revision == "2026-09-23T10:00:00Z"
    assert after_runbook.source_revision == "2026-09-24T09:00:00Z"
    assert "rollback script" in before_runbook.text
    assert "canary validation step" in after_runbook.text


def test_verifier_rejects_fabricated_citation_and_extractive_fallback_stays_authorised() -> None:
    evidence = InMemoryPipelineHarness(_provider_documents()).run(
        "What is the deployment process?", ALICE_USER_ID
    ).evidence
    fabricated = GeneratedAnswer(
        answer="Deployment uses an unsupported secret source [evi_deadbeef1234].",
        claims=[
            GeneratedClaim(
                text="Deployment uses an unsupported secret source.",
                evidence_ids=[_id("fabricated:evidence")],
            )
        ],
    )

    verification = verify_answer(fabricated, evidence)
    fallback = build_extractive_fallback(evidence, "What is the deployment process?")

    assert not verification.valid
    assert "unauthorised_citations" in verification.reason
    assert verify_answer(fallback, evidence).valid
    assert {claim.evidence_ids[0] for claim in fallback.claims} <= {ev.evidence_id for ev in evidence}
