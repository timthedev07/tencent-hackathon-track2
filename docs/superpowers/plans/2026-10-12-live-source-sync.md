# Live Source Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make fixture and live provider data visibly and operationally separate, and let an authenticated user queue ingestion for a real OAuth connection.

**Architecture:** A runtime `SOURCE_DATA_MODE` selects either fixture or delegated-OAuth connections at the identity and retrieval boundaries, without deleting either dataset. The connection API exposes the selected mode and stable connection IDs, while a tenant- and user-scoped sync endpoint queues the existing Celery backfill task only for delegated-OAuth connections. The frontend labels demo versus live data, blocks OAuth in fixture mode, and exposes `Sync now` only for real connections; query audit metadata reports the gateway actually used.

**Tech Stack:** FastAPI, SQLAlchemy 2, Pydantic Settings, Celery, PostgreSQL/pgvector, Next.js 16, React 19, TypeScript, Vitest, pytest.

**Spec:** `chronicle_prd_for_codex.md` and `data_population_plan.md`

## Global Constraints

- Fixture adapters must never be represented as live enterprise integrations.
- Retrieval, local identity resolution and connection status must use the same configured data mode.
- Sync requests must be tenant-scoped, require an exact verified identity link, and reject fixture connections.
- Provider credentials and tokens must never enter API responses, task messages, logs or committed files.
- Background ingestion continues through the existing `worker.tasks.backfill.run_backfill` task and `backfill` queue.
- Existing fixture mode remains the default so deterministic tests and the current demo keep working.

## Review Focus

- A user linked to both fixture and live connections must resolve only the configured mode; Task 1 tests this filter.
- Retrieval fallback paths must not reintroduce records from the other mode; Task 1 tests every SQL query builder, including exact-vector fallback.
- A connection ID from another tenant or user must not be distinguishable from a missing connection; Task 2 tests the fail-closed 404 path.
- Broker publication failure must return a safe retryable error rather than claiming that sync started; Task 2 tests the 503 path.
- The UI must never label fixture data as a live connection or allow it to be synced; Task 3 tests the rendered fixture and live states.

---

### Task 1: Runtime source-data mode isolation

**Files:**
- Create: `services/api/src/app/domain/source_mode.py`
- Modify: `.env.example`
- Modify: `services/api/src/app/settings.py`
- Modify: `services/api/src/app/auth/identities.py`
- Modify: `services/api/src/app/retrieval/vector.py`
- Modify: `services/api/src/app/retrieval/lexical.py`
- Modify: `services/api/src/app/retrieval/underfill.py`
- Modify: `services/api/src/app/retrieval/pipeline.py`
- Test: `tests/retrieval/test_source_mode.py`

**Interfaces:**
- Produces: `active_connection_mode() -> Literal["fixture", "delegated_oauth"]` and `SOURCE_DATA_MODE: Literal["fixture", "live"]`.
- Consumes: existing `SourceConnection.mode`, `SourceObject.source_connection_id`, and `get_settings()`.

- [ ] **Step 1: Write failing tests for mode mapping, identity-link SQL filtering and all retrieval SQL paths**

Tests assert that fixture maps to `fixture`, live maps to `delegated_oauth`, identity lookup joins and filters `source_connections.mode`, and vector, lexical, exact-vector fallback and searchable-content SQL all contain the active-mode predicate.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run pytest tests/retrieval/test_source_mode.py -q`

Expected: FAIL because `app.domain.source_mode` and mode predicates do not exist.

- [ ] **Step 3: Implement the runtime setting and apply it at identity and candidate-retrieval boundaries**

Add `SOURCE_DATA_MODE=fixture` to `.env.example`; join `SourceConnection` exactly once in each candidate query and filter by `active_connection_mode()`. Filter `find_identity_link()` by the same mode so fixture and live identity links cannot make each other ambiguous.

- [ ] **Step 4: Run focused and existing retrieval/security tests**

Run: `UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run pytest tests/retrieval tests/security -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .env.example services/api/src/app/domain/source_mode.py services/api/src/app/settings.py services/api/src/app/auth/identities.py services/api/src/app/retrieval tests/retrieval/test_source_mode.py docs/superpowers/plans/2026-10-12-live-source-sync.md
git commit -m "feat: isolate fixture and live source data"
```

### Task 2: Authorised live-connection sync API

**Files:**
- Create: `services/api/src/app/domain/source_connection_service.py`
- Modify: `services/api/src/app/api/routes/oauth.py`
- Test: `tests/api/test_source_connections.py`

**Interfaces:**
- Consumes: Task 1 `active_connection_mode()`, existing `AuthenticatedUser`, `IdentityLink`, `SourceConnection`, and Celery backfill task.
- Produces: connection response fields `connectionId`, `mode`, `canSync`, top-level `dataMode`, and `POST /v1/oauth/connections/{connection_id}/sync` returning `{connectionId, status: "queued"}` with HTTP 202.

- [ ] **Step 1: Write failing route/service tests**

Tests cover active-mode connection listing, fixture/live response metadata, exact verified-link enforcement, cross-user/cross-tenant 404, fixture rejection, successful `backfill` publication, and safe 503 publication failure.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run pytest tests/api/test_source_connections.py -q`

Expected: FAIL because the service, response fields and sync route do not exist.

- [ ] **Step 3: Implement listing, live-mode OAuth guard and sync publication**

Keep OAuth start endpoints unavailable in fixture mode with a structured 409 response. Queue only connection and tenant UUIDs; obtain provider credentials later inside the existing worker task.

- [ ] **Step 4: Run API, authentication and pipeline tests**

Run: `UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run pytest tests/api tests/auth tests/pipeline -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/api/src/app/domain/source_connection_service.py services/api/src/app/api/routes/oauth.py tests/api/test_source_connections.py
git commit -m "feat: queue authorised live source syncs"
```

### Task 3: Honest connection UI and Sync now control

**Files:**
- Modify: `apps/web/lib/api/client.ts`
- Modify: `apps/web/app/settings/page.tsx`
- Create: `apps/web/app/settings/page.test.tsx`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 2 `ConnectionsResponse` and sync endpoint.
- Produces: `syncConnection(connectionId: string)`, visible `Demo data`/`Live OAuth` badges, a fixture-mode explanation, and a working `Sync now` button for `canSync=true` connections.

- [ ] **Step 1: Write failing API-client and settings-page tests**

Tests assert that `syncConnection` sends authenticated POST, fixture cards say `Demo data` and have no sync button, live cards say `Live OAuth`, and clicking `Sync now` calls the API and reports queued status.

- [ ] **Step 2: Run the focused frontend tests and verify RED**

Run: `npm test -- --run lib/api/client.test.ts app/settings/page.test.tsx`

Expected: FAIL because the new response fields, labels and sync action do not exist.

- [ ] **Step 3: Implement the client and UI behavior and document live-mode startup**

Document `SOURCE_DATA_MODE=live`, restart requirements, OAuth completion, `Sync now`, and the requirement for `MODEL_API_KEY` when real generation is desired.

- [ ] **Step 4: Run frontend tests, lint, typecheck and production build**

Run: `npm test -- --run && npm run lint && npm run typecheck && npm run build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/api/client.ts apps/web/lib/api/client.test.ts apps/web/app/settings/page.tsx apps/web/app/settings/page.test.tsx README.md
git commit -m "feat: distinguish and sync live sources"
```

### Task 4: Truthful model execution metadata

**Files:**
- Modify: `services/api/src/app/models/gateway.py`
- Modify: `services/api/src/app/retrieval/pipeline.py`
- Test: `tests/retrieval/test_model_gateway_metadata.py`

**Interfaces:**
- Produces: `ModelGateway.generation_model_version` and `ModelGateway.embedding_model_version`; stub values are `stub-extractive-v1` and `deterministic-local-v1`, while HTTP values come from configured model names.
- Consumes: pipeline audit commit fields `QueryRun.model_version` and `QueryRun.embedding_version`.

- [ ] **Step 1: Write failing metadata tests**

Tests assert exact stub and HTTP gateway version properties and verify that audit commit stores supplied gateway versions instead of blindly storing configured OpenAI names.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run pytest tests/retrieval/test_model_gateway_metadata.py -q`

Expected: FAIL because gateway metadata properties and audit parameters do not exist.

- [ ] **Step 3: Implement gateway-owned metadata and pass it into audit commit**

Do not infer execution from a non-empty model name; the instantiated gateway is the authority.

- [ ] **Step 4: Run the complete backend quality suite**

Run: `UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run ruff format --check . && UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run ruff check . && UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run pyright && UV_CACHE_DIR=/private/tmp/tencent-hackathon-uv-cache uv run pytest -q`

Expected: PASS with the PostgreSQL/Celery-marked tests skipped when their explicit environment flag is absent.

- [ ] **Step 5: Commit**

```bash
git add services/api/src/app/models/gateway.py services/api/src/app/retrieval/pipeline.py tests/retrieval/test_model_gateway_metadata.py
git commit -m "fix: report the model gateway actually used"
```
