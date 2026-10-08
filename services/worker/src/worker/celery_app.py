"""Celery application with 11 priority queues.

Queues by priority (1 = highest):
1. deletions_priority
2. acl_priority
3. query_execution
4. webhook_ingest
5. source_fetch
6. normalisation
7. chunking
8. embeddings
9. reconciliation
10. backfill
11. audit_checkpoint / backtests
"""

from __future__ import annotations

from datetime import timedelta

from app.settings import get_settings
from celery import Celery
from kombu import Queue

settings = get_settings()

celery_app = Celery(
    "enterprise_assistant",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# ── Queue definitions (priority order) ──────────────────────────────
QUEUES = (
    Queue("deletions_priority", routing_key="deletions_priority", priority=1),
    Queue("acl_priority", routing_key="acl_priority", priority=2),
    Queue("query_execution", routing_key="query_execution", priority=3),
    Queue("webhook_ingest", routing_key="webhook_ingest", priority=4),
    Queue("source_fetch", routing_key="source_fetch", priority=5),
    Queue("normalisation", routing_key="normalisation", priority=6),
    Queue("chunking", routing_key="chunking", priority=7),
    Queue("embeddings", routing_key="embeddings", priority=8),
    Queue("reconciliation", routing_key="reconciliation", priority=9),
    Queue("backfill", routing_key="backfill", priority=10),
    Queue("audit_checkpoint", routing_key="audit_checkpoint", priority=11),
)

celery_app.conf.update(
    task_queues=QUEUES,
    task_default_queue="webhook_ingest",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_default_delivery_mode="persistent",
    task_time_limit=300,
    task_soft_time_limit=240,
    task_acks_on_failure_or_timeout=False,
    redis_max_connections=50,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "provider-reconciliation": {
            "task": "worker.tasks.reconcile.run_reconciliation",
            "schedule": timedelta(minutes=settings.RECONCILIATION_INTERVAL_MINUTES),
            "options": {"queue": "reconciliation"},
        }
    },
)

# ── Task routing ────────────────────────────────────────────────────
celery_app.conf.task_routes = {
    "worker.tasks.deletions.*": {"queue": "deletions_priority"},
    "worker.tasks.acl.*": {"queue": "acl_priority"},
    "worker.tasks.queries.*": {"queue": "query_execution"},
    "worker.tasks.webhook.*": {"queue": "webhook_ingest"},
    "worker.tasks.fetch.*": {"queue": "source_fetch"},
    "worker.tasks.normalise.*": {"queue": "normalisation"},
    "worker.tasks.chunk.*": {"queue": "chunking"},
    "worker.tasks.embed.*": {"queue": "embeddings"},
    "worker.tasks.reconcile.*": {"queue": "reconciliation"},
    "worker.tasks.backfill.*": {"queue": "backfill"},
    "worker.tasks.audit.*": {"queue": "audit_checkpoint"},
    "worker.tasks.backtests.*": {"queue": "audit_checkpoint"},
}

# Import each task module explicitly. Celery autodiscovery expects a
# ``tasks.py`` module inside each package, while this project keeps one task
# module per pipeline stage under ``worker.tasks``.
celery_app.conf.imports = (
    "worker.tasks.acl",
    "worker.tasks.audit",
    "worker.tasks.backfill",
    "worker.tasks.backtests",
    "worker.tasks.chunk",
    "worker.tasks.deletions",
    "worker.tasks.embed",
    "worker.tasks.fetch",
    "worker.tasks.normalise",
    "worker.tasks.queries",
    "worker.tasks.reconcile",
    "worker.tasks.webhook",
)
