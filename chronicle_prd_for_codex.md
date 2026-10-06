# Chronicle - Backtestable Internal Brain

**Document type:** Codex implementation PRD
**Target:** Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 - FinTech (Aspire)
**Status:** Build-ready MVP specification, aligned with the preliminary solution outline
**Primary demo domain:** `PAY-2026-042 - Payment Gateway Sev-1 Incident`

## 0. Instructions for Codex

Build the smallest repeatable system that proves the product and security invariants in this document.

- Treat sections marked **MUST** as acceptance criteria.
- Implement the **Core MVP** before any item marked **Production hardening** or **Stretch**.
- Use source-faithful synthetic fixtures by default. Keep connectors behind interfaces so real delegated OAuth connectors can replace them later.
- Do not weaken authorisation, temporal correctness, citation verification, or audit atomicity to simplify the demo.
- Do not claim production readiness merely because the MVP demonstrates the architecture.

## 1. Product statement

Chronicle is a permission-first enterprise knowledge assistant for regulated organisations. It answers cross-platform questions using evidence that the requester is authorised to see at the requested point in time, then makes the answer replayable and testable.

> Most enterprise assistants tell you what is known now. Chronicle can prove what was known, by whom, and why, at any point in time.

Chronicle is not a generic RAG chatbot. Its three first-class primitives are:

1. **Bitemporal Evidence Graph** - knowledge, identity and permissions have both a business-effective time and a system-observed time.
2. **Authorized Context Compiler** - identity, live source authorisation, policy and query time are compiled into a sealed evidence snapshot before any evidence text reaches the reranker or LLM.
3. **Knowledge Backtesting** - important questions are rerun across roles, historical times, document versions, ACLs and model-system versions to detect leakage and answer regressions.

## 2. Challenge fit

The FinTech challenge requires a context-aware enterprise knowledge system over Confluence, Jira, Slack and Google Drive while preserving each source's permission semantics.

The demo must show:

1. one natural-language query answered from multiple sources with citations;
2. fresh source updates reflected within minutes to roughly one hour;
3. a negative permission case without content or metadata leakage;
4. a permission revocation reflected on the next query;
5. an audit inquiry that reconstructs the query, access decisions, evidence and answer.

Required delivery evidence includes a working product/demo, source code, and an architecture/trust-boundary diagram.

## 3. System invariants - MUST

```text
Answer = f(
  query,
  authenticated_identity_at_t,
  live_permission_decisions,
  knowledge_at_t,
  policy_version,
  prompt_version,
  model_version,
  context_compiler_version
)
```

### 3.1 Security invariants

- **Fail closed:** missing, stale, timed-out or ambiguous identity/access evidence means deny that object or source.
- **No service-account fallback:** a failed delegated/user-specific check must never retry with broader credentials.
- **No unauthorised text before authorisation:** candidate text must not enter the reranker, context compiler, application logs or model request until that candidate passes the current query's authorisation check.
- **IDs before text:** pre-authorisation retrieval may return only internal candidate IDs and scores. It must not return content, snippets, titles, canonical URLs or source metadata to the model or end-user UI.
- **Query-scoped positive decisions:** do not reuse a positive source access decision across queries.
- **Citation reauthorisation:** clicking a citation performs a new access check because permission may have changed since answer generation.
- **Tenant isolation:** every source-derived row is tenant-scoped. The authenticated token, not request-body fields, determines tenant and user identity.

### 3.2 Temporal and answer invariants

- **No hindsight:** an `as_of` query cannot use a document, claim, identity link or permission state unavailable at that time.
- **Preserve conflict:** conflicting evidence must be surfaced, not silently merged into one convenient conclusion.
- **Evidence-bound claims:** every material claim must reference one or more allowed evidence IDs.
- **Safe fallback:** if structured-output or citation verification fails after one repair attempt, return an extractive evidence summary or no answer.

### 3.3 Audit invariants

- **Audit before response:** commit answer, access decisions, evidence manifest and chained audit event before emitting the final answer.
- **No answer on audit failure:** if the audit transaction fails, the query ends as `FAILED` and no final answer is shown.
- **Append-only evidence:** source versions and audit events are immutable at application level.
- **Independent checkpoint:** periodically sign and export the audit chain root outside the mutable audit tables. A database-only hash chain is not sufficient against full database rewrite.

## 4. Users and jobs

| User | Job | Required outcome |
|---|---|---|
| Engineer | Understand an active or historical incident | Current or point-in-time evidence-backed answer |
| Contractor | Ask within a restricted scope | No restricted content or existence metadata |
| Compliance officer | Investigate an answer/access event | Replay query, policy decision, evidence, output and chain status |
| Knowledge owner | Find stale or conflicting knowledge | Ranked finding with evidence and recommended human action |

## 5. Delivery scope: Core MVP vs production architecture

The architecture supports real connectors, but the hackathon build must prioritise a reliable demo.

### 5.1 Core MVP - MUST

- Source-faithful simulated adapters for Confluence, Jira, Slack and Google Drive.
- Each adapter implements the same connector protocol and simulates a live per-query permission check.
- One PostgreSQL database with `pgvector`; SQLite may be used only for isolated tests, not the final integration demo.
- FastAPI backend; Next.js/TypeScript frontend; generated OpenAPI client types.
- Redis/Celery only if required by ingestion latency or already available. A synchronous/local worker implementation is acceptable when it implements the same job interface.
- Bitemporal content and ACL versions.
- Hybrid candidate retrieval, live authorisation, reranking/context compilation, structured answer, citation verification and atomic audit commit.
- Evidence Capsule, hash-chain verification, signed checkpoint demonstration and Knowledge Backtesting.
- Deterministic fixture controls for content update and permission revocation.

### 5.2 Connector production path - Stretch

- Real delegated OAuth for one source is a valuable stretch goal.
- Real connectors for all four sources are not required for MVP completeness.
- A real connector is acceptable only if it can authoritatively check access as the current user for representative private objects.
- If a provider only supports a broad service account in the available environment, keep that connector in fixture mode and document the limitation.

### 5.3 Production hardening - NOT an MVP blocker

- Full OAuth token lifecycle and managed KMS envelope encryption.
- Webhooks/change feeds plus 15-30 minute reconciliation.
- PostgreSQL row-level security as defence in depth.
- Separate Celery queues, dead-letter handling and workload autoscaling.
- OpenTelemetry, Prometheus, SIEM integration and formal retention/deletion controls.
- Externally administered object storage and managed signing keys for audit checkpoints.

The MVP must preserve interfaces and invariants so these capabilities can be added without rewriting the domain model.

## 6. Demo dataset

Use synthetic data only; do not require real enterprise credentials.

### Incident: `PAY-2026-042 - Payment Gateway Sev-1`

| Source | Record | Key fact | Time / ACL |
|---|---|---|---|
| Confluence | `runbook-v1` | “Restart the database” | older; Engineering space |
| Confluence | `runbook-v2` | “Switch to failover cluster” | later; Engineering space |
| Slack | `#payments-incidents` message | expired certificate suspected | later; channel membership required |
| Jira | postmortem ticket | certificate rotation job failed | final resolution; Engineering + Incident team |
| Google Drive | security report | certificate details | Security + Compliance; contractor denied |

Fixture events:

- Contractor Bob is temporarily granted the security report, then access is revoked.
- The incident is closed without required security approval.
- A later document corrects an earlier root-cause assertion.
- A source update becomes visible after an ingestion action.

## 7. Architecture

### 7.1 Runtime topology

```text
apps/web (Next.js + TypeScript)
        |
generated OpenAPI client
        |
services/api (FastAPI + Pydantic)
        |-- Identity Resolver
        |-- Candidate Retriever (IDs and scores only)
        |-- Live Authorisation Gateway
        |-- Authorized Context Compiler
        |-- Model Gateway + Citation Verifier
        |-- Knowledge Auditor
        |-- Backtest Runner
        |-- Audit Service + Checkpoint Signer
        |
PostgreSQL + pgvector
        |
Connector Protocol
        |-- Confluence fixture / optional real adapter
        |-- Jira fixture / optional real adapter
        |-- Slack fixture / optional real adapter
        `-- Google Drive fixture / optional real adapter
```

Keep the MVP in one repository and one deployable backend unless measured need requires a split.

### 7.2 Suggested repository layout

```text
apps/web/                    # Next.js UI
services/api/                # FastAPI domain and HTTP API
services/worker/             # optional Celery or local worker entrypoint
packages/contracts/          # generated TypeScript client + JSON schemas
packages/fixtures/           # synthetic sources and event sequences
tests/security/              # no-leak and fail-closed tests
tests/backtests/             # golden questions across role/time/config
docs/architecture/           # trust boundaries and tradeoffs
```

### 7.3 Provider abstractions

```python
class SourceConnector(Protocol):
    async def initial_scan(self, cursor: str | None): ...
    async def read_changes(self, cursor: str | None): ...
    async def fetch_object(self, object_id: str): ...
    async def fetch_acl(self, object_id: str): ...
    async def check_access(self, user_identity: SourceIdentity, object_id: str): ...

class ModelGateway(Protocol):
    async def embed(self, texts: list[str], model_version: str): ...
    async def rerank(self, query: str, authorised_evidence: list[Evidence]): ...
    async def generate(self, authorised_context: AuthorizedContext, output_schema: dict): ...
```

Return typed connector outcomes: `success`, `not_found`, `forbidden`, `rate_limited`, `transient_error`, `permanent_error`. Provider SDK types must not leak into the domain layer.

Keep the LLM behind `ModelGateway`. Use a Tencent-provided model/API when entitlement is confirmed; do not hard-code an assumed free API or transfer CodeBuddy/WorkBuddy credits into inference credits.

## 8. Data model

Use relational tables as the implementation of the Evidence Graph; a graph database is not required.

### 8.1 Platform and source tables

```text
Tenant(id, name)
User(id, tenant_id, enterprise_subject, display_name)
SourceConnection(id, tenant_id, source, mode[fixture|delegated_oauth], status)
IdentityLink(id, user_id, source_connection_id, immutable_source_subject_id,
             valid_from, valid_to, observed_at, retired_at)
Principal(id, tenant_id, source, principal_type, external_principal_id)
SourceObject(id, tenant_id, source, external_object_id, object_type,
             canonical_url, parent_external_id, current_source_revision)
ObjectVersion(id, source_object_id, source_revision, normalised_content,
              content_hash, valid_from, valid_to, observed_at, retired_at)
Chunk(id, object_version_id, text, text_vector, embedding, embedding_model,
      chunk_hash, structural_path)
AclGrant(id, source_object_id, principal_id, permission, effect[allow|deny],
         inheritance_path, valid_from, valid_to, observed_at, retired_at)
```

An absent or ambiguous `IdentityLink` means deny for that source.

### 8.2 Evidence Graph and audit tables

```text
Claim(id, object_version_id, subject, predicate, object, confidence,
      valid_from, valid_to, observed_at, retired_at)
PolicyVersion(id, tenant_id, version, rules_json, effective_at, content_hash)
QueryRun(id, tenant_id, requester_id, query_text_ciphertext, as_of,
         status, policy_version, prompt_version, model_version,
         context_compiler_version, created_at, completed_at)
AccessDecision(id, query_run_id, source_object_id, decision[allow|deny],
               reason_code, access_checked_at)
EvidenceItem(id, query_run_id, object_version_id, evidence_id,
             quoted_span_ciphertext, content_hash, citation_label)
EvidenceCapsule(id, query_run_id, snapshot_hash, answer_ciphertext,
                evidence_manifest_json, created_at)
AuditEvent(id, tenant_id, query_run_id, event_type, canonical_payload_json,
           created_at, previous_hash, event_hash)
AuditCheckpoint(id, tenant_id, chain_head_hash, signed_root, signer_key_id,
                exported_location, created_at)
```

### 8.3 Bitemporal rules

Every mutable source fact/version/ACL/identity link carries:

| Dimension | Fields | Meaning |
|---|---|---|
| Valid time | `valid_from`, `valid_to` | When it was effective in the source/business world |
| System time | `observed_at`, `retired_at` | When Chronicle knew it and when that local version was superseded |

Versions are append-only. Corrections create a new version; they do not rewrite history.

## 9. Ingestion pipeline

### 9.1 State machine

```text
RECEIVED -> FETCHED -> NORMALISED -> ACL_RESOLVED -> CHUNKED -> EMBEDDED -> ACTIVE
```

- Persist state, attempt count, timestamp and stable error code.
- A new content version remains non-queryable until its ACL projection and embedding are ready.
- ACL changes and deletions have higher priority than content enrichment.
- A revocation immediately marks the local object ineligible, even if other processing is incomplete.
- Keep the previous `ACTIVE` version if the new version fails.
- Use stable idempotency key: `tenant:source:external_object_id:source_revision`.
- Preserve source-native object IDs, revisions, hierarchy and ACL principals.
- Chunk by stable structure: Confluence headings, Jira fields/comments, Slack thread boundaries and Drive document sections.

For fixtures, event playback must emulate the same states and update semantics as a real connector.

## 10. Query and authorisation pipeline

### 10.1 State machine

```text
AUTHENTICATED -> CANDIDATES -> REAUTHORISED -> RERANKED
              -> COMPILED -> GENERATED -> VERIFIED -> AUDITED
```

A query has exactly one terminal status: `COMPLETED`, `PARTIAL`, `DENIED`, or `FAILED`.

### 10.2 Required flow

1. Validate the user session and resolve tenant plus immutable source identity links.
2. Embed the query and run tenant-partitioned vector and full-text candidate searches.
3. Candidate retrieval returns only object/chunk IDs and scores.
4. Apply local ACL projection as a fast pre-filter.
5. Call `check_access()` for each top source object using the current user identity. Fixture adapters must simulate this call dynamically; real adapters use delegated credentials.
6. On timeout, expired consent or ambiguity, exclude that source/object. A labelled partial answer from other authorised sources is permitted.
7. Load content only for allowed object IDs.
8. Apply `as_of` bitemporal filtering, deduplicate versions and rerank allowed evidence.
9. Compile 8-12 token-budgeted evidence spans into an immutable Authorized Context.
10. Generate structured output: `answer`, `claims[]`, and `evidence_ids[]` per claim.
11. Deterministically reject unknown evidence IDs, filtered citations and material claims without evidence.
12. Commit answer, access decisions, evidence manifest and audit event in one transaction.
13. Emit the final answer only after step 12 succeeds.

### 10.3 Authorized Context

```json
{
  "requester": "eng_alice",
  "as_of": "2026-09-01T10:30:00Z",
  "policy_version": "v3",
  "context_compiler_version": "v1",
  "allowed_evidence": [
    {
      "evidence_id": "ev_123",
      "object_version_id": "objv_456",
      "content_hash": "sha256(...)"
    }
  ],
  "snapshot_hash": "sha256(canonical_context_json)"
}
```

Do not include denied titles, IDs, snippets, URLs, embeddings, counts or existence hints in the model request or employee UI. Deny details are restricted to authorised compliance audit views.

## 11. Functional requirements

### FR-1 - Unified cited answer

- Query all four source adapters through a common pipeline.
- Return one answer with claim-level citations.
- Surface conflicts and evidence chronology.
- Refuse or return uncertainty when evidence is insufficient.

### FR-2 - Point-in-time answer

- Accept optional `as_of` timestamp.
- Resolve knowledge, identity and ACL state bitemporally.
- A query before the final Jira postmortem exists must not cite it.
- The UI must distinguish “what was valid then” from “what Chronicle learned later.”

### FR-3 - Negative permission case

- Contractor Bob cannot access the Drive security report.
- His answer, source list, result count and audit-facing employee view reveal neither its contents nor existence.
- Tests inspect the captured model request and logs to prove the restricted text/metadata never entered either.

### FR-4 - Live permission revocation

- Admin/demo control revokes Bob's access.
- The next query executes a new `check_access()` and excludes the object.
- No cached positive decision or old model context is reused.
- Emit a `permission_changed` audit event.

### FR-5 - Fresh content update

- Admin/demo control publishes Jira postmortem v2 or Confluence runbook v2.
- The ingestion state machine activates the new version.
- Subsequent answers and evidence timelines reflect the new version without rewriting the old one.

### FR-6 - Evidence Capsule and audit replay

Compliance can inspect:

- requester, encrypted/raw-authorised query view and query time;
- `as_of`, policy, prompt, model and context-compiler versions;
- every allow/deny decision and reason code;
- authorised evidence revisions and hashes;
- answer, citations, latency and terminal status;
- audit chain and signed-checkpoint verification result.

The audit page is not a second unrestricted search surface. It requires the compliance role and redacts evidence the auditor is not entitled to view.

### FR-7 - Tamper evidence and independent checkpoint

Calculate:

```text
event_hash = SHA256(previous_hash + canonical_event_json)
```

MVP implementation:

- audit rows are append-only through application/database permissions;
- `/v1/audit/verify-chain` recomputes the chain and reports the first invalid event;
- periodically sign the tenant chain head with a demo signing key;
- write the signed checkpoint to a separate local volume/file store abstraction;
- demonstrate that modifying an audit row breaks verification against the exported checkpoint.

Production hardening replaces the demo signer/store with managed KMS and separately administered immutable object storage.

### FR-8 - Knowledge Auditor

Provide a companion surface for:

- **Conflict:** incompatible objects for the same `(subject, predicate)` across sources or versions.
- **Staleness:** an older runbook remains retrievable after a newer authoritative version is effective.
- **Access risk:** sensitive material is granted to a contractor.
- **Process gap:** a Sev-1 incident closes without required review, owner or approval.
- **Answer integrity:** a material answer claim lacks valid supporting evidence.

Each finding links to authorised evidence and recommends a human action. Chronicle does not automatically modify source content or permissions.

### FR-9 - Knowledge Backtesting

```text
BacktestCase(id, question, requester_id, as_of,
             expected_visibility, expected_citation_ids,
             expected_behavior, severity)
BacktestRun(id, suite_version, policy_version, prompt_version,
            model_version, embedding_version, reranker_version,
            context_compiler_version, started_at)
BacktestResult(run_id, case_id, passed, answer_hash,
               citation_score, leakage_check, drift_class, notes)
```

Run backtests when a fixture/document, ACL, policy, prompt, model, embedding, reranker or context-compiler version changes.

Detect:

- newly leaked restricted content or metadata;
- historical answers using future knowledge;
- missing/changed citations;
- unsupported claims;
- failure to disclose conflicting evidence;
- material answer drift for a critical question.

For MVP, use a small curated deterministic suite. Do not claim statistical model evaluation or production-scale monitoring.

## 12. API contract

Prefer asynchronous query execution so the final answer is emitted only after verification and audit commit.

```http
POST /v1/queries
{ "query": "What caused the payment outage?", "asOf": "2026-09-01T10:30:00Z" }
-> { "queryId": "qry_123" }

GET /v1/queries/{queryId}/events
-> server-sent state events; final answer event only after AUDITED

GET /v1/queries/{queryId}
-> status, answer, citations, partial-source warnings

GET /v1/evidence/{evidenceId}
-> reauthorise current user, then return allowed span

GET /v1/audit/{queryId}
-> compliance-only Evidence Capsule and chain status

POST /v1/audit/verify-chain
POST /v1/audit/checkpoints
POST /v1/backtests/runs
GET  /v1/backtests/runs/{runId}

POST /v1/demo/fixtures/events/{eventId}/apply
-> deterministic source update or permission change
```

Do not accept `tenant_id`, source user IDs or arbitrary roles from the normal query request body. A clearly labelled demo-only identity switcher may mint predefined local sessions; it must not be wired into production authentication code.

## 13. UI pages

1. **Ask Chronicle** - query, predefined demo identity switcher, `now/as-of` picker, cited response, uncertainty/partial warning and evidence timeline.
2. **Knowledge Auditor** - conflict, staleness, access risk, process gap and answer-integrity findings.
3. **Access Simulator** - explain allow/deny and applicable policy without displaying denied content.
4. **Audit Replay** - Evidence Capsule, access decisions, configuration versions, hash chain and checkpoint status.
5. **Backtesting** - baseline vs current results, leakage failures, historical correctness and citation/answer drift.
6. **Demo Controls** - apply content update, grant/revoke permission, create checkpoint and launch backtest.

## 14. Demo script

1. Alice asks: “What caused the Payment Gateway outage?” Chronicle cites the final Jira postmortem and surfaces the older conflicting runbook/Slack evidence.
2. Select an earlier `as_of` time. The final Jira conclusion disappears; Chronicle reports the uncertainty that genuinely existed then.
3. Switch to contractor Bob. He asks the same question; the restricted Drive report is absent without a metadata side channel.
4. Apply a permission grant event and show Bob can use the report; revoke it and show the next query excludes it.
5. Publish the updated Jira/Confluence fixture and show freshness plus append-only version history.
6. Open the Evidence Capsule; replay the query and verify the audit hash chain plus exported signed checkpoint.
7. Change a runbook, ACL, policy or prompt version; run Knowledge Backtesting and show a deliberate leakage/citation/drift regression.
8. Open Knowledge Auditor and show conflict, stale runbook, access risk and missing incident approval.

## 15. Tests - MUST

### Security tests

- Denied object text/title/URL does not appear in model request, reranker input, context snapshot, response or logs.
- Ambiguous identity link denies that source.
- Source timeout produces `PARTIAL` or `DENIED`, never broader access.
- Positive access decision from query A is not reused by query B.
- Citation view rechecks access and redacts after revocation.
- Final answer is not emitted when audit commit is forced to fail.

### Temporal tests

- Historical query does not cite future content.
- Late-observed correction preserves original system-time history.
- ACL grant/revocation is resolved at the correct valid/system time.

### Audit tests

- Evidence Capsule contains all required configuration versions and evidence hashes.
- Modified audit row breaks the chain.
- Rewritten database chain does not match the previously exported signed checkpoint.

### Backtest tests

- Deliberately loosened contractor ACL fails leakage case.
- Removed citation fails support case.
- Replacing current runbook with v1 fails staleness/current-answer case.
- An `as_of` answer using the Jira postmortem early fails no-hindsight case.

## 16. Explicit non-goals for the hackathon MVP

- Four production OAuth integrations.
- Claiming that fixture adapters are live enterprise integrations.
- Full SSO/IAM, DLP, legal hold, retention compliance or external SIEM integration.
- Kubernetes, Kafka or microservice decomposition.
- Perfect automatic truth determination; conflict detection supports human review.
- Using prompt instructions as a substitute for authorisation.
- Automatically changing permissions or source documents.

## 17. Implementation order and exit criteria

### Stage 0 - Security skeleton

- Implement connector protocol, predefined identities, dynamic fixture `check_access()` and a captured model-request test.
- **Exit:** allow, deny and next-query revocation pass for all four fixture sources; no denied text reaches the model fixture.

### Stage 1 - Foundation and bitemporal ingestion

- Create monorepo, FastAPI, Next.js, PostgreSQL/pgvector, migrations, generated contracts, fixtures and ingestion state machine.
- **Exit:** version update, ACL update and tenant isolation tests pass.

### Stage 2 - Query pipeline

- Implement IDs-only hybrid retrieval, live authorisation, authorised text loading, reranking, context compilation, model gateway and citation verification.
- **Exit:** golden queries pass across users and times.

### Stage 3 - Atomic audit and replay

- Implement Evidence Capsule, audit transaction, hash chain, signed checkpoint and compliance replay.
- **Exit:** answer can be reconstructed; audit failure suppresses final answer; tampering is detected.

### Stage 4 - Knowledge audit and backtesting

- Implement deterministic findings and role/time/config regression suite.
- **Exit:** deliberate leakage, hindsight, citation and drift regressions are detected.

### Stage 5 - Demo hardening

- Add UI, scripted fixture events, graceful partial-source behaviour and repeatable end-to-end test.
- **Exit:** all required competition scenarios and the full Chronicle differentiator demo run without manual database changes.

### Optional Stage 6 - One real connector

- Implement one delegated OAuth adapter only after Core MVP is stable.
- **Exit:** representative private object allow/deny/revocation works without service-account bypass.

## 18. Definition of done

The MVP is complete when a reviewer can run it locally or through a deployed URL and observe all of the following:

- Four simulated source adapters contribute to one cited answer.
- Candidate retrieval is IDs-only until current-query authorisation passes.
- A historical query proves no future-knowledge leakage.
- A negative permission case reveals no protected content or metadata.
- A revocation changes the next answer without reusing cached permission/context.
- An update becomes active through the ingestion state machine while prior versions remain replayable.
- The final answer is emitted only after citation verification and audit commit.
- Audit Replay reconstructs evidence, access decisions, versions and output.
- Hash-chain tampering and mismatch with an exported signed checkpoint are detectable.
- Knowledge Auditor flags at least one conflict, stale record, access risk and process gap.
- Knowledge Backtesting detects at least one deliberate leakage or answer regression.
- Repository contains setup instructions, fixture data, security tests, architecture/trust-boundary diagram and retained CodeBuddy/WorkBuddy usage evidence.
