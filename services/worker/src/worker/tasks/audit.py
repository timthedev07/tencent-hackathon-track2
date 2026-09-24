"""Audit checkpoint task — periodically sign and upload chain roots to S3."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.persistence.models import Tenant
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select

from worker.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker.tasks.audit.checkpoint_all_tenants")
def checkpoint_all_tenants() -> dict[str, Any]:
    """Create S3 checkpoints for every tenant's audit chain."""
    import asyncio

    return asyncio.run(_checkpoint_all_tenants())


async def _checkpoint_all_tenants() -> dict[str, Any]:
    from app.audit.checkpoint import create_checkpoint
    from app.audit.verification import verify_chain

    factory = get_session_factory()
    results: list[dict[str, Any]] = []

    async with factory() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()

    for tenant in tenants:
        async with factory() as session:
            await set_tenant_context(session, tenant.id)
            chain_ok = await verify_chain(session, tenant.id)
            if not chain_ok:
                logger.error("Audit chain verification failed", tenant_id=str(tenant.id))
                results.append({"tenantId": str(tenant.id), "status": "failed_verification"})
                continue
            checkpoint = await create_checkpoint(session, tenant.id)
            results.append(checkpoint)

    logger.info("Checkpoint run complete", tenant_count=len(results))
    return {"tenants": results}
