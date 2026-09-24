# App Summary — Tencent Hackathon Enterprise AI Assistant

> **Note:** This file is a persistent in-repo copy of the agent memory (knowledge_id: `10095013`). If the memory is lost or a fresh agent is spawned, this file ensures the app context survives. Keep in sync with the memory when the architecture changes.

---

## Purpose

Internal enterprise AI assistant for **permission-safe cross-platform retrieval** across Confluence, Jira, Slack, and Google Drive. An authenticated enterprise user asks a natural-language question; the system retrieves candidate evidence, checks the exact user's live permissions, and generates one grounded answer with source citations.

Core promise: retrieval quality matters, but **security takes precedence** — restricted content must never reach LLM/reranker/logs/browser.

## Architecture (Monorepo)

- **Next.js** web app (frontend, SSE, citation viewer, compliance audit viewer)
- **FastAPI** API (auth, query orchestration, retrieval, authorisation, generation, audit)
- **Celery** worker (webhook ingestion, initial scans, reconciliation, chunking, embeddings)
- **PostgreSQL** + **pgvector** (authoritative state, vector search, FTS, RLS)
- **Redis** (Celery broker, locks, ephemeral state — never authoritative)
- **S3-compatible object store** (MinIO for dev; signed audit-chain checkpoints)
- **Model gateway** abstraction (embeddings, reranking, generation)

No Kubernetes, Kafka, or microservice decomposition for the hackathon MVP.

## Security Invariants (INV-001..009)

| ID | Invariant |
|---|---|
| INV-001 | No unauthorised text beyond the authorisation boundary (only IDs/scores/ACL fields cross pre-auth) |
| INV-002 | Ambiguity = deny (missing identity, expired creds, timeout, malformed response → deny) |
| INV-003 | Positive access is query-scoped (no reuse across queries; in-memory only for one query) |
| INV-004 | No service-account fallback when user-scoped checks fail |
| INV-005 | Final answer requires successful audit commit (no answer before audit persists) |
| INV-006 | Citation display is reauthorised on open (revoked → no title/text leak) |
| INV-007 | Tenant identity is server-derived (never trust client `tenant_id`) |
| INV-008 | Source revisions are immutable locally (append-only `object_versions`) |
| INV-009 | Redis is not authoritative for content/ACLs/audit/versions |

## Domain Enums

- **Source:** `confluence`, `jira`, `slack`, `google_drive`
- **QueryStatus:** `created → authenticated → candidates → reauthorised → reranked → compiled → generated → verified → audited → completed | partial | denied | failed`
- **IngestionStatus:** `received → fetched → normalised → acl_resolved → chunked → embedded → active | quarantined | failed`
- **AccessOutcome:** `allow`, `deny`, `unknown`, `timeout`, `expired_credential`, `identity_missing`

## Database Schema (17 tables, all with RLS)

`tenants`, `users`, `source_connections`, `encrypted_credentials`, `identity_links`, `source_objects`, `object_versions` (immutable), `chunks` (pgvector embedding + FTS tsv), `principals`, `acl_grants`, `ingestion_cursors`, `webhook_deliveries`, `query_runs`, `access_decisions`, `evidence_items`, `audit_events` (append-only, hash-chained), plus ingestion state tracking.

## Query Pipeline (Stages A–J)

| Stage | Description |
|---|---|
| A — Auth | OIDC validate → resolve tenant/user → open tenant-scoped DB txn |
| B — Candidate retrieval | Vector top-50 + FTS top-50, tenant-constrained, IDs only |
| C — Local ACL projection | Optimisation (not authoritative); local deny may reject early |
| D — Live reauthorisation | Top-30 objects, delegated user creds, ~1.5s budget, per-provider semaphore |
| E — Authorised text load | Only after live allow; deduplicate superseded versions; cap per source |
| F — Reranking | 8–12 chunks; source diversity ≤50% per source; semantic + authority + recency |
| G — Context compilation | Deterministic, token-budgeted, evidence-tagged XML; prompt-injection defence |
| H — Generation | Low temp, tools disabled, structured output: `GeneratedAnswer{answer, claims[{text, evidence_ids}]}` |
| I — Verification | Deterministic: all evidence IDs must exist in authorised context; repair once then extractive fallback |
| J — Audit commit | Atomic txn: query state + encrypted answer + access decisions + evidence + audit chain event |

## Connector Protocol

`initial_scan`, `read_changes`, `fetch_object`, `fetch_acl`, `check_access`. Outcomes: `success`, `not_found`, `forbidden`, `rate_limited`, `transient_error`, `permanent_error`.

## Ingestion

Append-only versions; idempotency key `{tenant}:{source}:{object}:{revision}`; activation only after normalise + ACL + chunks + embeddings commit. Deletions/revocations have higher priority than enrichment. Previous active version retained until replacement is fully ready.

## Webhooks

`POST /v1/webhooks/{source}` → verify signature → validate schema → deduplicate → persist safe metadata → enqueue idempotent task.

## Retrieval

Hybrid: pgvector HNSW + PostgreSQL FTS. Reciprocal Rank Fusion (RRF, k=60). Underfill fallback to exact scan. Source diversity cap (≤50% from one source).

## Latency Budget

| Stage | Target |
|---|---|
| Auth + candidate retrieval | ~250 ms |
| Live source authorisation | ~1,500 ms |
| Rerank + context compilation | ~1,000 ms |
| Generation | ~6,000 ms |
| Audit commit + finalisation | ~500 ms |
| **End-to-end target** | **< 10 s** |

Deadline breach → degrade to partial/failed, never bypass access checking.

## Audit

Tamper-evident hash chain: `SHA-256(canonical_json(event) || previous_hash)`. External signed checkpoints to S3. Application DB role cannot `UPDATE`/`DELETE` `audit_events`.

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/queries` | Create query |
| `GET` | `/v1/queries/{id}/events` | SSE progress stream |
| `GET` | `/v1/queries/{id}` | Fetch final answer + citations |
| `GET` | `/v1/evidence/{id}` | Fetch evidence (reauthorises on open) |
| `GET` | `/v1/audit/{id}` | Compliance audit capsule (compliance role only) |
| `GET` | `/health/live` | Liveness |
| `GET` | `/health/ready` | Readiness (DB, Redis, migrations) |

## Fixture Model (Section 51)

Tenant A with users Alice, Bob, compliance user (Carol). Public + restricted objects across all 4 sources. Scripted permission-change fixtures for revocation, content update, deletion, and ACL change scenarios.

## 5 Demo Scenarios

1. **Authorised cross-source query** — answer uses evidence from ≥2 sources with correct citations
2. **Restricted query** — denied text absent from reranker/LLM/logs/browser
3. **Permission revocation** — next query excludes revoked object; old citation returns `access_revoked`
4. **Fresh-data update** — new revision becomes active; old version retained
5. **Audit lookup** — compliance user retrieves capsule; chain verification passes; tampering detected

## Implementation Stages

0. Security feasibility spike (prove delegated access checking for all 4 sources)
1. Foundation (monorepo, FastAPI, Celery, PostgreSQL/pgvector, RLS, OIDC, observability)
2. Ingestion (connectors, webhooks, normalisation, chunking, embeddings, activation)
3. Retrieval and query path (hybrid search, RRF, local ACL, live auth, rerank, context, generation, verification)
4. Audit and backtesting (capsule, hash chain, checkpoints, compliance endpoint)
5. Hardening and demo (failure injection, P0 fail-closed, demo repeatability)

## P0 Engineering Priorities

- Delegated per-user access checking
- No unauthorised text before live check
- Revocation on next query
- Identity mapping
- Citation reauthorisation
- Audit-before-response
- Tenant isolation
- Audit-chain integrity

## Central Engineering Principle

> **Authorisation is part of retrieval, not a post-processing filter.**

Everything else in the system should be designed around preserving that property.

## Key Files

- Spec: `tencent_hackathon_implementation_spec.md`
- Seed dataset: `packages/fixtures/seed_dataset.json`
- Source fixtures: `packages/fixtures/{confluence,jira,slack,drive}/`
- Seed script: `scripts/seed_fixtures.py`
- Env template: `.env.example`
- Credential setup guide: `docs/credentials-setup.md`
