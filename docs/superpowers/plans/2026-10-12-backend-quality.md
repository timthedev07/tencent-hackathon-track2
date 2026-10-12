# Backend Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the documented host runtime configuration consistent and make the repository's backend format, lint, type-check and default test gates pass locally and in GitHub Actions.

**Architecture:** Keep the Docker network on PostgreSQL port 5432 while documenting the published host port 5434 for host-run processes. Fix type and lint findings at their source, preserve connector behavior, add the missing tracing runtime dependencies to the uv lock, then enforce the same commands in a dedicated hosted quality workflow.

**Tech Stack:** Python 3.12+, uv, Ruff, Pyright, pytest, Docker Compose, GitHub Actions.

**Spec:** `PROJECT_ANALYSIS.md`

## Global Constraints

- Preserve the retrieval-before-authorisation security boundary and fail-closed connector outcomes.
- Do not place provider source text in logs, traces, exceptions or test output.
- Keep Docker service-to-service PostgreSQL traffic on `postgres:5432`; use `localhost:5434` only for host processes.
- Keep dependencies locked with `uv.lock` and install them with `uv sync --frozen --all-extras` in CI.
- Do not modify or include the user's uncommitted `services/api/src/app/api/routes/health.py` change from the primary checkout.

## Review Focus

- Host-run API commands must connect to the Compose-published PostgreSQL port 5434, while container services continue using 5432; Task 1's config consistency check covers both paths.
- Tracing configured with a non-empty OTLP endpoint must import and construct its SDK components; Task 2 adds and runs a focused tracing configuration test.
- Valid Fernet keys must be used directly and malformed keys must still fall back to deterministic derivation; Task 2's credential tests cover both branches.
- Provider payloads containing missing or malformed nested objects must not cause unsafe optional access; Task 3 runs Pyright and the provider-shaped pipeline suite after typed normalization.
- HTTP and OAuth status classification must retain its existing allow, deny, expired, rate-limited and error semantics; Task 3 runs the security and pipeline suites after replacing magic numbers.

---

### Task 1: Align host PostgreSQL configuration

**Files:**
- Modify: `.env.example`
- Modify: `docs/credentials-setup.md`
- Modify: `PROJECT_ANALYSIS.md`

**Interfaces:**
- Consumes: `docker-compose.yml` host mapping `5434:5432` and container overrides using `postgres:5432`.
- Produces: documented host connection URL `postgresql+asyncpg://assistant:assistant@localhost:5434/assistant`.

- [ ] **Step 1: Run the config consistency check and confirm it fails**

Run a read-only Python assertion that compares the Compose published port with `.env.example` and `docs/credentials-setup.md`.

Expected: FAIL because both host-facing files currently use 5432.

- [ ] **Step 2: Update host-facing configuration and documentation**

Change only host-facing PostgreSQL examples to port 5434. Keep Compose-internal URLs on `postgres:5432`. Update the corresponding project-analysis gap and priority list.

- [ ] **Step 3: Re-run config validation**

Run the consistency assertion and `docker compose config --quiet`.

Expected: both commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add .env.example docs/credentials-setup.md PROJECT_ANALYSIS.md
git commit -m "fix: align local database port"
```

### Task 2: Restore dependency and core utility type correctness

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `pyrightconfig.json`
- Modify: `services/api/src/app/observability/tracing.py`
- Modify: `services/api/src/app/auth/credentials.py`
- Modify: `services/api/src/app/auth/identities.py`
- Modify: `services/api/src/app/connectors/errors.py`
- Modify: `services/api/src/app/connectors/http.py`
- Create: `tests/observability/test_tracing.py`
- Create: `tests/auth/test_credentials.py`

**Interfaces:**
- Consumes: `Settings.OTEL_EXPORTER_OTLP_ENDPOINT`, Fernet's 32-byte URL-safe key contract, ORM source-connection identifiers, and provider Retry-After values.
- Produces: installable OTLP SDK/exporter imports, typed credential-key validation, an identity-link boundary accepting the ORM identifier representation, and a fractional retry delay that matches runtime behavior.

- [ ] **Step 1: Add focused failing tests**

Add `test_configure_tracing_with_endpoint_builds_provider`, `test_valid_fernet_key_is_used_directly`, and `test_invalid_fernet_key_uses_derived_key`. Ensure tracing cleanup shuts down the provider and resets module state.

- [ ] **Step 2: Run the focused tests and current Pyright check**

Expected: tracing test fails because the SDK/exporter packages are missing; Pyright reports the credentials, identity, retry-delay and tracing import errors.

- [ ] **Step 3: Implement the minimal fixes**

Add `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`, refresh `uv.lock`, remove the unsupported Pyright `$schema` key, use `binascii.Error` with a non-raising Fernet-key validator, align identity-link input typing with the ORM boundary, and make `RateLimitedError.retry_after` accept the float returned by the bounded parser.

- [ ] **Step 4: Verify the focused tests and affected static checks**

Run the focused pytest files, Ruff on the affected modules, and Pyright.

Expected: focused tests pass; the core utility/import errors are absent. Remaining connector errors are recorded for Task 3.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock pyrightconfig.json services/api/src/app/observability/tracing.py services/api/src/app/auth/credentials.py services/api/src/app/auth/identities.py services/api/src/app/connectors/errors.py services/api/src/app/connectors/http.py tests/observability/test_tracing.py tests/auth/test_credentials.py
git commit -m "fix: complete backend runtime typing dependencies"
```

### Task 3: Make connectors and Ruff checks clean

**Files:**
- Modify: `services/api/src/app/connectors/confluence.py`
- Modify: `services/api/src/app/connectors/jira.py`
- Modify: `services/api/src/app/connectors/google_drive.py`
- Modify: `services/api/src/app/connectors/slack.py`
- Modify: `services/api/src/app/connectors/oauth.py`
- Modify: `tests/pipeline/test_isolated_query_pipeline.py`
- Modify: other Python files reported by `ruff format --check .` only through deterministic Ruff formatting.

**Interfaces:**
- Consumes: provider JSON as untrusted mappings/lists and existing `AccessOutcome` classifications.
- Produces: explicitly narrowed provider payload values and status comparisons expressed with `http.HTTPStatus` without changing connector outcomes.

- [ ] **Step 1: Preserve the failing static-analysis evidence**

Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright`.

Expected: the remaining Ruff violations, formatting drift, and connector type errors are reproduced.

- [ ] **Step 2: Narrow untrusted nested payload values**

Bind each nested provider value once, validate its mapping/list shape with `isinstance`, and use typed empty fallbacks before access or iteration. Keep malformed-data behavior fail-closed or empty rather than throwing.

- [ ] **Step 3: Remove lint findings without suppressions**

Use `HTTPStatus` for protocol status comparisons, named constants or a helper for cryptographic sizes, split the composite assertion, and run Ruff formatting. Exclude `chronicle_prd_for_codex.md` from Ruff formatting because it is a product plan rather than Python source.

- [ ] **Step 4: Run static and behavior verification**

Run Ruff format check, Ruff lint, Pyright, the provider-shaped pipeline suite, and the security suite.

Expected: static commands exit 0 and both pytest suites pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml services/api/src services/worker/src tests
git commit -m "style: make backend quality checks green"
```

Do not stage `chronicle_prd_for_codex.md`; its explicit formatter exclusion prevents a product-plan rewrite.

### Task 4: Enforce backend quality in hosted CI

**Files:**
- Create: `.github/workflows/backend-quality.yml`
- Modify: `PROJECT_ANALYSIS.md`

**Interfaces:**
- Consumes: the locked Python environment and the same `make ci` commands used locally.
- Produces: a GitHub Actions quality gate on pull requests and pushes to `dev` and `jess`.

- [ ] **Step 1: Add the hosted workflow**

Use Ubuntu 24.04, Python 3.12, pinned `setup-uv` v10.3.0, `uv sync --frozen --all-extras`, and `make ci` with a 15-minute timeout and read-only contents permission.

- [ ] **Step 2: Update the progress analysis**

Record that runtime port alignment and repository-wide backend format, lint, type and default test checks are enforced in hosted CI. Keep remaining integration, browser and production gaps unchanged.

- [ ] **Step 3: Run complete local verification**

Run `make ci`, YAML parsing for all workflows, `docker compose config --quiet`, and `git diff --check`.

Expected: every command exits 0; default pytest reports 43 or more passing tests and four infrastructure-gated skips.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/backend-quality.yml PROJECT_ANALYSIS.md
git commit -m "ci: enforce backend quality checks"
```

- [ ] **Step 5: Push and verify hosted workflows**

Fast-forward the completed isolated branch into `jess`, push `jess`, wait for backend quality, backend integration and frontend quality to finish, and fix any hosted-only failure before completion.
