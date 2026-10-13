.PHONY: install dev worker lint typecheck test test-integration test-security \
       format format-check migrate openapi frontend-install frontend-dev \
       frontend-format frontend-format-check \
       frontend-build frontend-lint frontend-test e2e docker-up docker-down \
       clean

CELERY_POOL ?= $(if $(filter Darwin,$(shell uname -s)),solo,prefork)
CELERY_CONCURRENCY ?= $(if $(filter solo,$(CELERY_POOL)),1,2)
PRETTIER_TARGETS := '**/*.{js,jsx,ts,tsx,mjs,mts,cjs,cts,json,css,scss,yml,yaml}'

# ── Python ──────────────────────────────────────────────────────────
install:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	uv run celery -A worker.celery_app worker --loglevel=info --pool=$(CELERY_POOL) --concurrency=$(CELERY_CONCURRENCY)

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .
	$(MAKE) frontend-format

format-check:
	uv run ruff format --check .
	$(MAKE) frontend-format-check

frontend-format:
	apps/web/node_modules/.bin/prettier --ignore-path .prettierignore --write $(PRETTIER_TARGETS)

frontend-format-check:
	apps/web/node_modules/.bin/prettier --ignore-path .prettierignore --check $(PRETTIER_TARGETS)

typecheck:
	uv run pyright

test:
	uv run pytest

test-integration:
	uv run pytest tests/integration -v

test-security:
	uv run pytest tests/security -v

# ── Database ────────────────────────────────────────────────────────
migrate:
	uv run alembic upgrade head

migrate-new:
	uv run alembic revision --autogenerate -m "$(MSG)"

# ── OpenAPI / Frontend contracts ────────────────────────────────────
openapi:
	uv run python scripts/generate_openapi.py
	cd apps/web && yarn generate-types

# ── Frontend ───────────────────────────────────────────────────────
frontend-install:
	cd apps/web && yarn install

frontend-dev:
	cd apps/web && yarn dev

frontend-build:
	cd apps/web && yarn build

frontend-lint:
	cd apps/web && yarn lint

frontend-test:
	cd apps/web && yarn test

e2e:
	cd apps/web && yarn e2e

# ── Docker ──────────────────────────────────────────────────────────
docker-up:
	docker compose up -d

docker-down:
	docker compose down

# ── CI (run everything) ─────────────────────────────────────────────
ci: format-check lint typecheck test test-security
	@echo "CI checks passed."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf services/api/.mypy_cache services/worker/.mypy_cache
