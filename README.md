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
