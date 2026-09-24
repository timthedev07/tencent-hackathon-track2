# This file documents the enforced engineering standards for the project.
# All subagents and developers MUST follow these strictly.

## 1. Code Formatting

### Frontend (apps/web)
- **Prettier** with the root `.prettierrc.json` configuration.
- Run: `yarn prettier --check .` (CI) / `yarn prettier --write .` (local)
- No exceptions for formatting — the formatter is authoritative.

### Backend (services/api, services/worker)
- **Ruff format** with the root `ruff.toml` configuration.
- Run: `uv run ruff format .` (local) / checked in CI.
- Line length: 100 characters.

## 2. Linting

### Frontend
- **ESLint** with Next.js recommended config.
- Run: `yarn lint` (local) / checked in CI.
- `eslint-config-next` as base; no `any` types allowed; no unused variables.

### Backend
- **Ruff check** (includes flake8-bandit for security, flake8-bugbear, isort, etc.).
- **Pyright** in strict mode.
- Run: `uv run ruff check .` and `uv run pyright`.
- Both must pass with zero errors before merge.

## 3. Security and Integrity Practices

### SQL Injection Prevention
- **All** database queries MUST use SQLAlchemy parameterised queries or ORM methods.
- **Never** use f-strings, string concatenation, or `.format()` for SQL construction.
- Raw SQL (when unavoidable) MUST use bound parameters: `text("... WHERE id = :id").bindparams(id=value)`.

### CSRF Protection
- All state-changing API endpoints (POST/PUT/PATCH/DELETE) require:
  - A valid OIDC Bearer token (checked by middleware).
  - Same-origin enforcement via `Origin` / `Referer` header validation for browser-initiated requests.
- Webhook endpoints verify provider signatures instead of OIDC tokens.

### Database Transactions
- **Never** hold a DB transaction open across slow external calls (provider APIs, model calls).
- Use short, scoped transactions for: query creation, access-decision persistence, final audit commit.
- The final answer + evidence manifest + audit event MUST commit atomically in one transaction.
- RLS: `SET LOCAL app.tenant_id = '<uuid>'` at the start of every transaction.

### Credential Encryption
- Envelope encryption: AES-256-GCM data key + KMS-wrapped key material.
- Decrypt only in process memory, immediately before use; discard plaintext after.
- **Never** log: refresh tokens, access tokens, authorisation headers, OAuth codes, KMS plaintext.

### Webhook Security
- Verify provider signature BEFORE any processing.
- Deduplicate by `(source, provider_delivery_id)` unique constraint.
- Invalid signature/replay/malformed → reject before enqueueing.

### Audit Integrity
- `audit_events` table: application role has INSERT + scoped SELECT only.
- Application role MUST NOT have UPDATE or DELETE on `audit_events`.
- SHA-256 hash chain: `event_hash = SHA-256(canonical_json(event) || previous_hash)`.

### Content Safety
- Candidate text MUST NOT enter reranker, context compiler, LLM request, logs, tracing, or browser responses until the object passes live authorisation.
- No source text in span attributes, log fields, or error messages.

## 4. British English Spellings

All code, comments, documentation, UI text, error messages, and variable names MUST use British English spellings. Examples:
- `authorise` / `authorised` / `authorisation` (not `authorize`)
- `colour` (not `color`)
- `behaviour` (not `behavior`)
- `optimise` (not `optimize`)
- `normalise` (not `normalize`)
- `minimise` (not `minimize`)
- `catalogue` (not `catalog`)
- `centre` (not `center`)
- `licence` (noun) / `license` (verb)
- `analyse` (not `analyze`)
- `paralyse` (not `paralyze`)
- `synchronise` (not `synchronize`)
- `recognise` (not `recognize`)

Exception: Third-party library API names (e.g., `color` in CSS/Tailwind) remain as-is.

## 5. Package Managers
- Python: `uv` exclusively. `pyproject.toml` + `uv.lock` committed.
- Frontend: `yarn` exclusively (NOT npm). `yarn.lock` committed.
- Never use `pip install` directly in CI; always `uv sync` first.

## 6. Dependency Rule
```
api/routes → domain/use-cases → interfaces/protocols → infrastructure
```
- Provider SDK types, HTTP payload types, and Celery task payloads MUST NOT leak into core domain objects.
- Domain objects use Pydantic models; connector implementations translate provider responses into domain types at the boundary.

## 7. Testing Standards
- Unit tests: pytest + pytest-asyncio (backend), Vitest (frontend).
- Integration tests: Testcontainers (PostgreSQL+pgvector, Redis).
- Connector contract tests: respx for HTTP mocking.
- E2E tests: Playwright.
- Security invariant tests (SEC-001 through SEC-010) MUST pass before merge.
- A retrieval test that finds the right answer using denied evidence is a FAILED test.

## 8. Observability Standards
- Logging: structlog JSON format. Only permitted fields (request_id, query_id, tenant_id, etc.).
- Tracing: OpenTelemetry. 21 named spans. No source text in span attributes.
- Metrics: Prometheus. 17 named metrics with exact label cardinalities.

## 9. CI Pipeline (per PR)
1. Backend lint (ruff check)
2. Backend format check (ruff format --check)
3. Static type check (pyright)
4. Backend unit tests (pytest)
5. Integration tests (Testcontainers)
6. Frontend lint/typecheck (eslint + tsc)
7. OpenAPI regeneration diff check
8. Frontend tests (vitest)
9. Security invariant tests (SEC-001 through SEC-010)

Merge MUST be blocked if security invariant tests fail.
