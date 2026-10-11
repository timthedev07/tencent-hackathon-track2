# ============================================================================
# Tencent Hackathon — Internal Enterprise AI Assistant
# Implementation Plan
# ============================================================================
#
# This file is the authoritative implementation plan for subagents.
# It explicitly instructs parallel execution to maximise efficiency.
# All subagents MUST follow docs/standards.md strictly.
# ============================================================================

## Overview

Permission-safe cross-platform enterprise AI assistant spanning Confluence, Jira, Slack, and Google Drive. The core product promise: **permission-safe cross-platform retrieval**. Security takes precedence over recall or latency. Restricted source content must never reach the LLM, reranker, logs, or browser.

## Technology Stack

- **Backend**: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + asyncpg, Celery, Redis, PostgreSQL + pgvector, Alembic, structlog, OpenTelemetry
- **Frontend**: Next.js (App Router), TypeScript, Tailwind, shadcn/ui, Zod, openapi-typescript, Playwright
- **Tooling**: uv (Python), npm (frontend), Ruff, Pyright strict, pytest, Testcontainers, respx

## Database Schema (17 tables — all with RLS)

1. `tenants` — id, external_org_key, display_name, created_at, updated_at
2. `users` — id, tenant_id, oidc_subject, display_name, email, is_compliance_user, created_at, updated_at
3. `source_connections` — id, tenant_id, source, external_workspace_id, display_name, status, last_sync_at, last_reconcile_at
4. `encrypted_credentials` — id, tenant_id, user_id, source_connection_id, credential_type, ciphertext, key_version, expires_at, scope, created_at, updated_at
5. `identity_links` — id, tenant_id, user_id, source_connection_id, provider_subject_id, provider_account_type, verified_at, verified_by, status
6. `source_objects` — id, tenant_id, source_connection_id, source, external_object_id, object_type, parent_external_id, canonical_url, current_revision, current_version_id, is_deleted, is_queryable
7. `object_versions` — id, tenant_id, source_object_id, source_revision, normalised_title, normalised_content, content_hash, valid_from, valid_to, observed_at, retired_at, ingestion_status
8. `chunks` — id, tenant_id, object_version_id, ordinal, section_path, text, tsv, embedding, embedding_model, chunk_hash, token_count, created_at
9. `principals` — id, tenant_id, source_connection_id, principal_type, provider_principal_id, display_name
10. `acl_grants` — id, tenant_id, source_object_id, principal_id, permission, grant_effect, inheritance_path, source_revision, observed_at
11. `ingestion_cursors` — id, tenant_id, source_connection_id, cursor_type, cursor_value, updated_at
12. `webhook_deliveries` — id, tenant_id, source, provider_delivery_id, signature_valid, received_at, processed_at, status, payload_hash, safe_metadata
13. `query_runs` — id, tenant_id, user_id, query_ciphertext, status, partial_sources, prompt_version, model_version, retriever_version, reranker_version, context_policy_version, started_at, completed_at, latency_ms, error_code, answer_ciphertext
14. `access_decisions` — id, query_id, tenant_id, source_object_id, source, decision, reason_code, access_checked_at, provider_latency_ms, provider_request_id
15. `evidence_items` — id, query_id, tenant_id, chunk_id, source_object_id, source_revision, source, title_snapshot, canonical_url, quoted_span_ciphertext, rank, retrieval_score, rerank_score
16. `audit_events` — id, tenant_id, query_id, sequence_no, event_type, canonical_payload, previous_hash, event_hash, created_at (INSERT-only)
17. (dead_letter_tasks — operational table for dead-letter handling)

## Indexes

- `source_objects`: (tenant_id, source, external_object_id), (tenant_id, is_queryable), (tenant_id, source_connection_id)
- `object_versions`: (source_object_id, source_revision), (tenant_id, observed_at DESC), (tenant_id, ingestion_status)
- `chunks`: BTREE(tenant_id), GIN(tsv), HNSW(embedding), UNIQUE(object_version_id, ordinal)
- `acl_grants`: (tenant_id, source_object_id), (tenant_id, principal_id), (source_object_id, permission)
- `query_runs`: (tenant_id, user_id, started_at DESC), (tenant_id, status)
- `access_decisions`: (query_id), (tenant_id, source_object_id)
- `evidence_items`: (query_id, rank)
- `audit_events`: (tenant_id, sequence_no), (query_id)

## Celery Queues (10, by priority)

1. deletions_priority (highest)
2. acl_priority
3. webhook_ingest
4. source_fetch
5. normalisation
6. chunking
7. embeddings
8. reconciliation
9. backfill
10. audit_checkpoint / backtests

## Query Pipeline (10 stages)

A. Authentication (OIDC validate → resolve tenant/user → open RLS transaction)
B. Candidate retrieval (vector top-50 + FTS top-50, tenant-constrained, IDs only)
C. Reciprocal-rank fusion (RRF, k=60, deterministic)
D. Local ACL projection (optimisation; local allow never skips live check)
E. Live reauthorisation (top-30 objects, per-provider semaphores=8, ~1.5s budget)
F. Authorised text load (first time text appears in pipeline)
G. Reranking (8-12 chunks, source diversity cap ≤50%)
H. Context compilation (deterministic, token-budgeted, prompt-injection defence)
I. Generation (low temperature, structured output, GeneratedAnswer schema)
J. Verification (deterministic: evidence ID validation, repair once, extractive fallback)
K. Audit commit (one transaction: state + answer + evidence + audit event → then SSE)

## API Endpoints

- `POST /v1/queries` — create query (no tenant_id in body)
- `GET /v1/queries/{queryId}/events` — SSE stream
- `GET /v1/queries/{queryId}` — final result with citations
- `GET /v1/evidence/{evidenceId}` — citation (reauthorised)
- `GET /v1/audit/{queryId}` — compliance audit capsule
- `POST /v1/webhooks/{source}` — webhook receiver
- `GET /health/live` + `GET /health/ready`

## Error Codes (20 stable)

AUTH_INVALID_TOKEN, AUTH_TENANT_NOT_RESOLVED, SOURCE_IDENTITY_MISSING, SOURCE_CREDENTIAL_EXPIRED, SOURCE_ACCESS_FORBIDDEN, SOURCE_ACCESS_TIMEOUT, SOURCE_RATE_LIMITED, SOURCE_FETCH_FAILED, SOURCE_OBJECT_NOT_FOUND, ACL_AMBIGUOUS, INGESTION_PARSE_FAILED, INGESTION_EMBED_FAILED, RETRIEVAL_UNDERFILLED, RERANK_FAILED, MODEL_GENERATION_FAILED, MODEL_OUTPUT_INVALID, CITATION_INVALID, AUDIT_COMMIT_FAILED, AUDIT_CHAIN_INVALID, EVIDENCE_ACCESS_REVOKED

## Implementation Phases

### Phase 1 — Standards & Scaffolding (serial, must complete first)
- .editorconfig, ruff.toml, pyrightconfig.json, .prettierrc, eslint config
- docs/standards.md
- pyproject.toml (uv), docker-compose.yml, .env.example, Makefile, README.md
- Monorepo directory structure (apps/web, services/api, services/worker, packages/, alembic/, tests/, infra/)
- Dockerfiles for api/worker (shared image) and web

### Phase 2 — Database & Config (serial, depends on Phase 1)
- SQLAlchemy 2 models for all 17 tables
- Alembic migration(s) creating all tables, RLS policies, indexes
- pydantic-settings Settings class with all env vars
- RLS session middleware (SET LOCAL app.tenant_id)

### Phase 3 — Foundation & Identity (serial, depends on Phase 2)
- FastAPI app bootstrap (main.py, router registration, exception handlers)
- Celery app with 10 queues + priority routing
- OIDC validation (PyJWT, JWKS cache, issuer/audience/expiry checks)
- RLS middleware (resolve tenant from token, set local setting)
- structlog JSON logger with sanitised fields
- OpenTelemetry tracing setup (21 spans)
- Prometheus metrics endpoint (17 metrics)
- OpenAPI JSON export + openapi-typescript generation
- source_connections CRUD
- encrypted_credentials with envelope encryption (AES-256-GCM)
- identity_links management
- OAuth callback flows: Atlassian, Slack, Google

### Phase 4-6 — PARALLEL IMPLEMENTATION (3 subagents simultaneously)

#### Subagent 1: Connectors & Ingestion (Epics C + D)
- Connector Protocol (initial_scan, read_changes, fetch_object, fetch_acl, check_access)
- Typed connector errors (success, not_found, forbidden, rate_limited, transient, permanent)
- Confluence connector (page ID, space, version, ancestor hierarchy, heading-based chunking)
- Jira connector (issue key, project, security metadata, summary/description/comments chunking)
- Slack connector (channel ID, thread, permalink, thread-based chunking, no cross-channel merge)
- Google Drive connector (file ID, MIME type, permission principals, document structure chunking)
- Webhook delivery model + signature verification
- Idempotency framework ({tenant}:{source}:{object_id}:{revision})
- Initial scan, change feed, reconciliation scheduler (15-30 min)
- Normalisation (NormalisedObject, ContentBlock, deterministic)
- Chunking (300-800 tokens, SHA-256 chunk hash, embedding reuse)
- Embedding pipeline (dimension validation, model version storage)
- Activation transaction (normalise → ACL → chunks → embeddings → atomic activate)
- Dead-letter handling (bounded retries, admin-only visibility)

#### Subagent 2: Query Pipeline (Epic E)
- Query run state machine (CREATED → ... → COMPLETED/PARTIAL/DENIED/FAILED)
- Vector search (pgvector, top-50, tenant-constrained)
- Lexical search (PostgreSQL FTS, top-50, tenant-constrained)
- Reciprocal-rank fusion (k=60, deterministic)
- Local ACL projection (optimisation, conservative deny)
- Live authorisation (top-30 objects, per-provider semaphores=8, ~1.5s deadline, deny on timeout)
- Authorised text loader (first text in pipeline, dedup superseded versions)
- Reranker (8-12 chunks, source diversity ≤50%, source authority + recency features)
- Context compiler (deterministic, token-budgeted, <evidence> XML tags, injection defence)
- Model generation (low temp, structured output, GeneratedAnswer/GeneratedClaim)
- Output verifier (evidence ID validation, repair once, extractive fallback)
- SSE events stream (11 event types, no sensitive metadata)

#### Subagent 3: Evidence, Audit & Frontend (Epics F + G)
- Evidence items persistence
- Evidence reauthorisation endpoint (fresh provider check, deny → access_revoked)
- Access decision persistence (allow + deny)
- Evidence capsule (25+ fields, reconstructable)
- Audit hash chain (SHA-256, canonical JSON, genesis marker)
- External S3 checkpoints (signed root, separate key)
- Compliance endpoint (compliance role required, exact query ID, chain verification)
- Chain verifier (detect tampering)
- Next.js auth/session management
- Query UI (input, progress states, answer, citations)
- SSE progress display (7 human-readable states)
- Partial-source UX (warning banner, no hint of excluded content)
- Connection settings page
- Compliance audit viewer (restricted role)

### Phase 7 — Tests & Fixtures (after all code)
- Synthetic fixtures (tenant A, alice/bob/compliance user, all source objects with scripted permission changes)
- Security invariant suite (SEC-001 through SEC-010)
- Connector contract suite (16 cases × 4 connectors)
- Golden retrieval set (recall@50, recall@30, recall@10, MRR, citation precision)
- Playwright 5 demo scenarios (automated, deterministic fixtures)
- Failure injection harness (9 failure modes)
- Reset/demo scripts

### Phase 8 — Integration Audit
- All imports resolve
- Types pass (pyright strict + tsc --noEmit)
- API contracts match (FastAPI OpenAPI ↔ generated TS client)
- Alembic migrations consistent
- Celery task payloads match schema
- No provider SDK types in domain objects

### Phase 9 — Security & Performance Audit
- INV-001 through INV-009 verified
- All 10 SEC tests pass
- RLS on all tenant tables
- INSERT-only audit_events verified
- No secret/content in logs
- Envelope encryption verified
- Async everywhere (no event-loop blocking)
- HNSW underfill fallback implemented
- Provider concurrency caps (8 per source)
- Short transactions (no long-held locks)
- Query latency budget adherence (<10s target)

## Parallel Execution Instructions for Subagents

When multiple subagents work simultaneously:
1. Each subagent works ONLY in its assigned directory scope.
2. Shared dependencies (domain types, protocols, settings) are established in Phase 3 before parallel work begins.
3. Subagents MUST NOT modify files outside their scope.
4. Each subagent MUST follow docs/standards.md (British English, Ruff, Pyright, security invariants).
5. After parallel implementation, a merge/integration pass resolves any conflicts.
