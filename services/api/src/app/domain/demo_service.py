"""Deterministic fixture event playback for the Chronicle demo."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.chain import append_event
from app.auth.oidc import AuthenticatedUser
from app.models.gateway import deterministic_embedding
from app.persistence.models import (
    AclGrant,
    Chunk,
    FixtureEvent,
    IdentityLink,
    ObjectVersion,
    Principal,
    SourceConnection,
    SourceObject,
    User,
)
from app.persistence.session import get_session_factory, set_tenant_context
from app.retrieval.temporal import active_now
from app.settings import get_settings


async def apply_fixture_event(event_key: str, user: AuthenticatedUser) -> dict:
    """Apply one fixture event exactly once and audit the resulting state change."""
    if not get_settings().is_development:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        event = (
            await session.execute(
                select(FixtureEvent)
                .where(
                    FixtureEvent.tenant_id == user.tenant_id,
                    FixtureEvent.event_key == event_key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "FIXTURE_EVENT_NOT_FOUND"}},
            )
        if event.status == "applied":
            return {
                "eventId": event_key,
                "status": "already_applied",
                "appliedAt": event.applied_at.isoformat() if event.applied_at else None,
            }

        payload = event.payload
        event_type = str(payload.get("type"))
        if event_type in {"permission_grant", "permission_revoke"}:
            outcome = await _apply_permission_event(session, user.tenant_id, payload)
            audit_type = "permission_changed"
        elif event_type == "content_update":
            outcome = await _apply_content_update(session, user.tenant_id, payload)
            audit_type = "content_updated"
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": {"code": "FIXTURE_EVENT_UNSUPPORTED"}},
            )

        applied_at = datetime.now(UTC)
        event.status = "applied"
        event.applied_at = applied_at
        await append_event(
            session=session,
            tenant_id=user.tenant_id,
            event_type=audit_type,
            canonical_payload={"fixture_event": event_key, **outcome},
            query_id=None,
        )
        await session.commit()
        return {
            "eventId": event_key,
            "status": "applied",
            "appliedAt": applied_at.isoformat(),
            **outcome,
        }


async def _fixture_object(
    session: AsyncSession, tenant_id: UUID, payload: dict
) -> tuple[SourceObject, UUID]:
    row = (
        await session.execute(
            select(SourceObject, SourceConnection.id)
            .join(SourceConnection, SourceObject.source_connection_id == SourceConnection.id)
            .where(
                SourceObject.tenant_id == tenant_id,
                SourceObject.source == payload["source"],
                SourceObject.external_object_id == payload["externalObjectId"],
                SourceConnection.mode == "fixture",
            )
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "FIXTURE_OBJECT_NOT_FOUND"}},
        )
    return row[0], row[1]


async def _apply_permission_event(session: AsyncSession, tenant_id: UUID, payload: dict) -> dict:
    source_object, connection_id = await _fixture_object(session, tenant_id, payload)
    target_user = await session.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.oidc_subject == payload["subject"],
        )
    )
    if target_user is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "FIXTURE_USER_NOT_FOUND"}})
    identity = await session.scalar(
        select(IdentityLink).where(
            IdentityLink.user_id == target_user.id,
            IdentityLink.source_connection_id == connection_id,
            active_now(IdentityLink),
        )
    )
    if identity is None:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "FIXTURE_IDENTITY_NOT_FOUND"}},
        )
    principal = await session.scalar(
        select(Principal).where(
            Principal.source_connection_id == connection_id,
            Principal.provider_principal_id == identity.provider_subject_id,
        )
    )
    if principal is None:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "FIXTURE_PRINCIPAL_NOT_FOUND"}},
        )

    active_grants = list(
        (
            await session.scalars(
                select(AclGrant).where(
                    AclGrant.source_object_id == source_object.id,
                    AclGrant.principal_id == principal.id,
                    AclGrant.grant_effect == "allow",
                    active_now(AclGrant),
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    if payload["type"] == "permission_grant" and not active_grants:
        session.add(
            AclGrant(
                tenant_id=tenant_id,
                source_object_id=source_object.id,
                principal_id=principal.id,
                permission="read",
                grant_effect="allow",
                source_revision=source_object.current_revision,
                valid_from=now,
                observed_at=now,
            )
        )
    elif payload["type"] == "permission_revoke":
        for grant in active_grants:
            grant.valid_to = now
            grant.retired_at = now

    return {
        "change": payload["type"],
        "source": source_object.source,
        "sourceObjectId": str(source_object.id),
        "subject": payload["subject"],
    }


async def _apply_content_update(session: AsyncSession, tenant_id: UUID, payload: dict) -> dict:
    source_object, _ = await _fixture_object(session, tenant_id, payload)
    existing = await session.scalar(
        select(ObjectVersion).where(
            ObjectVersion.source_object_id == source_object.id,
            ObjectVersion.source_revision == payload["revision"],
        )
    )
    if existing is None:
        now = datetime.now(UTC)
        previous = await session.scalar(
            select(ObjectVersion).where(ObjectVersion.id == source_object.current_version_id)
        )
        if previous is not None:
            previous.valid_to = now
            previous.retired_at = now
        content = str(payload["content"])
        version = ObjectVersion(
            tenant_id=tenant_id,
            source_object_id=source_object.id,
            source_revision=payload["revision"],
            normalised_title=payload.get("title"),
            normalised_content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            valid_from=datetime.fromisoformat(payload["validFrom"].replace("Z", "+00:00")),
            observed_at=now,
            ingestion_status="active",
        )
        session.add(version)
        await session.flush()
        settings = get_settings()
        session.add(
            Chunk(
                tenant_id=tenant_id,
                object_version_id=version.id,
                ordinal=0,
                section_path=[str(payload.get("title") or "Updated content")],
                text=content,
                embedding=deterministic_embedding(content, settings.EMBEDDING_DIMENSION),
                embedding_model=settings.EMBEDDING_MODEL,
                chunk_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                token_count=max(1, len(content) // 4),
            )
        )
        source_object.current_version_id = version.id
        source_object.current_revision = version.source_revision
        source_object.is_queryable = True
        source_object.is_deleted = False
    else:
        version = existing

    return {
        "change": "content_update",
        "source": source_object.source,
        "sourceObjectId": str(source_object.id),
        "objectVersionId": str(version.id),
        "revision": version.source_revision,
    }
