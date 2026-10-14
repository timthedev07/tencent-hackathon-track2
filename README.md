# Tencent Hackathon — Internal Enterprise AI Assistant

Permission-safe cross-platform enterprise AI assistant spanning Confluence, Jira, Slack, and Google Drive.

## Core Promise

**Permission-safe cross-platform retrieval.** Security takes precedence over recall or latency. Restricted source content must never reach the LLM, reranker, logs, or browser.

## Quick Start

```bash
# 1. Install dependencies
make install
make frontend-install

# 2. Copy env and start infrastructure
cp .env.example .env
docker compose up -d postgres redis minio

# 3. Run database migrations
make migrate

# 4. Start the API and frontend
make dev          # API on :8000
make frontend-dev # Web on :3000

# 5. Run the Celery worker and scheduler
make worker
```

## Development Commands

| Command | Description |
|---|---|
| `make install` | Install Python dependencies via uv |
| `make frontend-install` | Install frontend dependencies via yarn |
| `make dev` | Start FastAPI dev server |
| `make frontend-dev` | Start Next.js dev server |
| `make worker` | Run asynchronous ingestion tasks and schedule provider reconciliation |
| `make beat` | Run the scheduler separately when the worker is managed independently |
| `make lint` | Lint backend (ruff) |
| `make format` | Format Python with Ruff and supported source files with Prettier |
| `make format-check` | Check Python and supported source-file formatting without changing files |
| `make typecheck` | Type-check backend (pyright strict) |
| `make test` | Run backend unit tests |
| `make test-security` | Run security invariant tests (SEC-001–010) |
| `make migrate` | Run Alembic migrations |
| `make openapi` | Regenerate OpenAPI + TS client |
| `make e2e` | Run Playwright end-to-end tests |
| `make ci` | Run full CI pipeline locally |

## Architecture

- **Browser**: Next.js (App Router) — sign-in, query UI, SSE progress, citations, audit viewer
- **API**: FastAPI — auth, query orchestrator, candidate retrieval, live authorisation, model gateway, output verifier, audit
- **Worker**: Celery — webhook ingestion, initial scans, reconciliation, normalisation, chunking, embeddings, deletion handling, audit checkpoints
- **Database**: PostgreSQL + pgvector (authoritative state, RLS, hybrid search, audit chain)
- **Queue**: Redis (Celery broker + ephemeral state only — never authoritative)
- **Object Store**: S3-compatible (signed audit-chain checkpoints)

## Standards

See [`docs/standards.md`](docs/standards.md) for the full set of enforced standards including:
- Prettier, ESLint, Ruff, Pyright strict
- Security: SQL injection prevention, CSRF, envelope encryption, INSERT-only audit
- British English spelling consistency
- Package managers: uv (Python), yarn (frontend)

## Implementation Plan

See [`implementation_plan.md`](implementation_plan.md) for the detailed implementation plan.

## Short-lived demo deployment

The repository includes [`render.yaml`](render.yaml) for a lowest-cost demo deployment on Render. It uses free web services, free PostgreSQL with pgvector, and free Redis-compatible Key Value. Render's free PostgreSQL database expires after 30 days, and free services can sleep when idle, so this setup is for demonstrations only.

Create the Render Blueprint once, then disable Render auto-deploys. The [`CI/CD workflow`](.github/workflows/ci-cd.yml) runs the Python and web checks and, after they pass on `dev` or `main`, triggers and waits for both Render deployments before smoke-testing them.

Set these GitHub Actions secrets:

- `RENDER_API_KEY` — a Render API key from Account Settings → API Keys.
- `RENDER_API_SERVICE_ID` — the API service ID, in the Render service URL or service settings.
- `RENDER_WEB_SERVICE_ID` — the web service ID, in the Render service URL or service settings.

Set these GitHub Actions variables:

- `DEMO_API_URL` — for example, `https://relay-demo-api.onrender.com`.
- `DEMO_WEB_URL` — for example, `https://relay-demo-web.onrender.com`.

Set these Render environment values during Blueprint creation:

- `MODEL_API_KEY` — required for model-backed answers.
- `WEB_BASE_URL` and `CORS_ALLOWED_ORIGINS` — set both to the deployed web URL.
- `NEXT_PUBLIC_API_URL` — set to the deployed API URL, then redeploy once.

GitHub's built-in `GITHUB_TOKEN` is sufficient for checkout; no GitHub personal access token or Docker registry credential is required. Provider credentials for Atlassian, Slack, Google Drive, OIDC, or S3-compatible object storage are only needed if those integrations are demonstrated.

The demo runs with `APP_ENV=development` so the built-in sign-in screen works without configuring an OIDC provider. Do not use this configuration for real users or sensitive data.
