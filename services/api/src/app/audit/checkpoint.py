"""External S3 signed checkpoints for the audit hash chain.

At regular intervals: calculate tenant chain root, sign/MAC it with a
key NOT administered by the app DB role, write to S3-compatible store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import boto3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger
from app.persistence.models import AuditCheckpoint, AuditEvent
from app.settings import get_settings

logger = get_logger(__name__)


def _get_checkpoint_mac_key() -> bytes:
    settings = get_settings()
    signing_key = settings.AUDIT_CHECKPOINT_SIGNING_KEY
    if not signing_key:
        if settings.is_production:
            raise RuntimeError("AUDIT_CHECKPOINT_SIGNING_KEY is required in production")
        signing_key = settings.ENCRYPTION_MASTER_KEY
    base = signing_key.encode("utf-8")
    salt = b"audit-checkpoint-mac-v1"
    return hashlib.scrypt(base, salt=salt, n=2**14, r=8, p=1, dklen=32)


def _sign_root(root_hash: str) -> str:
    key = _get_checkpoint_mac_key()
    return hmac.new(key, root_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _get_s3_client() -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.OBJECT_STORE_ENDPOINT,
        aws_access_key_id=settings.OBJECT_STORE_ACCESS_KEY,
        aws_secret_access_key=settings.OBJECT_STORE_SECRET_KEY,
        region_name="us-east-1",
    )


async def get_tenant_chain_root(session: AsyncSession, tenant_id: UUID) -> str | None:
    stmt = (
        select(AuditEvent.event_hash)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.sequence_no.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_checkpoint(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    root_hash = await get_tenant_chain_root(session, tenant_id)
    if root_hash is None:
        return {"tenantId": str(tenant_id), "status": "skipped", "reason": "empty_chain"}

    signature = _sign_root(root_hash)
    checkpoint = {
        "tenantId": str(tenant_id),
        "chainRoot": root_hash,
        "signature": signature,
        "algorithm": "HMAC-SHA256",
        "createdAt": datetime.now(UTC).isoformat(),
    }

    object_key = f"checkpoints/{tenant_id}/{datetime.now(UTC).isoformat()}.json"
    settings = get_settings()

    # Run synchronous boto3 call in executor to avoid blocking the event loop
    import asyncio

    def _upload() -> None:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=settings.OBJECT_STORE_BUCKET,
            Key=object_key,
            Body=json.dumps(checkpoint, sort_keys=True).encode("utf-8"),
            ContentType="application/json",
        )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _upload)

    exported_location = f"s3://{settings.OBJECT_STORE_BUCKET}/{object_key}"
    session.add(
        AuditCheckpoint(
            tenant_id=tenant_id,
            chain_head_hash=root_hash,
            signed_root=signature,
            signer_key_id="demo-hmac-v1",
            exported_location=exported_location,
        )
    )

    from app.audit.chain import append_event

    await append_event(
        session=session,
        tenant_id=tenant_id,
        event_type="checkpoint.created",
        canonical_payload={
            "objectKey": object_key,
            "chainRoot": root_hash,
            "signature": signature,
            "bucket": settings.OBJECT_STORE_BUCKET,
        },
        query_id=None,
    )
    logger.info("Audit checkpoint written", tenant_id=str(tenant_id), object_key=object_key)
    return {
        "tenantId": str(tenant_id),
        "objectKey": object_key,
        "exportedLocation": exported_location,
        "chainHead": root_hash,
        "signedRoot": signature,
        "status": "written",
    }


async def verify_latest_checkpoint(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    """Verify the latest separately exported root against the retained chain."""
    checkpoint = (
        await session.execute(
            select(AuditCheckpoint)
            .where(AuditCheckpoint.tenant_id == tenant_id)
            .order_by(AuditCheckpoint.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if checkpoint is None:
        return {"present": False, "valid": False, "reason": "checkpoint_missing"}

    signature_valid = hmac.compare_digest(
        checkpoint.signed_root,
        _sign_root(checkpoint.chain_head_hash),
    )
    retained_hash = (
        await session.execute(
            select(AuditEvent.event_hash).where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.event_hash == checkpoint.chain_head_hash,
            )
        )
    ).scalar_one_or_none()
    chain_root_present = retained_hash is not None
    return {
        "present": True,
        "valid": signature_valid and chain_root_present,
        "signatureValid": signature_valid,
        "chainRootPresent": chain_root_present,
        "chainHead": checkpoint.chain_head_hash,
        "exportedLocation": checkpoint.exported_location,
        "createdAt": checkpoint.created_at.isoformat(),
    }
