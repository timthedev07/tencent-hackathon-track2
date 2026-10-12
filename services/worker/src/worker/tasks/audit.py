"""Audit checkpoint task — periodically sign and upload chain roots to S3."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.observability.logging import get_logger
from app.persistence.models import Tenant
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.audit.checkpoint_all_tenants")  # type: ignore[misc]
def checkpoint_all_tenants() -> dict[str, Any]:
    """Create S3 checkpoints for every tenant's audit chain."""
    from worker.async_runner import run_async

    return run_async(_checkpoint_all_tenants())


async def _checkpoint_all_tenants() -> dict[str, Any]:
    from app.audit.checkpoint import create_checkpoint
    from app.audit.verification import verify_chain

    factory = get_session_factory()
    results: list[dict[str, Any]] = []

    async with factory() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()

    for tenant in tenants:
        async with factory() as session:
            tenant_id = UUID(tenant.id)
            await set_tenant_context(session, tenant_id)
            chain_ok = await verify_chain(session, tenant_id)
            if not chain_ok:
                logger.error("Audit chain verification failed", tenant_id=str(tenant_id))
                results.append({"tenantId": str(tenant_id), "status": "failed_verification"})
                continue
            checkpoint = await create_checkpoint(session, tenant_id)
            results.append(checkpoint)

    logger.info("Checkpoint run complete", tenant_count=len(results))
    return {"tenants": results}
