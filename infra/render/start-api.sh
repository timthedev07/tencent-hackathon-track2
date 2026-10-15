#!/bin/sh

set -eu

# The free Render plan has no background-worker instance. Run the demo worker
# and Beat beside the API so queries and scheduled ingestion both run.
uv run alembic upgrade head
uv run celery -A worker.celery_app worker --loglevel=info --pool=solo --concurrency=1 --beat &
worker_pid=$!

cleanup() {
  kill "$worker_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

exec uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
