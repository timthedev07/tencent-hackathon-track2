"""Celery application with 10 priority queues.

Queues by priority (1 = highest):
1. deletions_priority
2. acl_priority
3. webhook_ingest
4. source_fetch
5. normalisation
6. chunking
7. embeddings
8. reconciliation
9. backfill
10. audit_checkpoint / backtests
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
    Queue("webhook_ingest", routing_key="webhook_ingest", priority=3),
    Queue("source_fetch", routing_key="source_fetch", priority=4),
    Queue("normalisation", routing_key="normalisation", priority=5),
    Queue("chunking", routing_key="chunking", priority=6),
    Queue("embeddings", routing_key="embeddings", priority=7),
    Queue("reconciliation", routing_key="reconciliation", priority=8),
    Queue("backfill", routing_key="backfill", priority=9),
    Queue("audit_checkpoint", routing_key="audit_checkpoint", priority=10),
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

# Autodiscover tasks
celery_app.autodiscover_tasks(
    [
        "worker.tasks",
    ]
)

import worker.tasks  # type: ignore[unused-import]  # noqa: F401, E402
