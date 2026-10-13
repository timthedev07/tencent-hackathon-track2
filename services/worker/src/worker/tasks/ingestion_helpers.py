"""Shared, tenant-safe helpers for provider ingestion tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.connectors.confluence import ConfluenceConnector, get_atlassian_accessible_resources
from app.connectors.google_drive import GoogleDriveConnector
from app.connectors.jira import JiraConnector
from app.connectors.oauth import pack_tokens, refresh_access_token, unpack_tokens
from app.connectors.protocol import Connector
from app.connectors.slack import SlackConnector
from app.domain.types import (
    DelegatedCredential,
    Source,
    SourceConnection,
    SourceConnectionStatus,
)
from app.persistence.models import EncryptedCredential, IdentityLink
from app.persistence.models import SourceConnection as SourceConnectionRow
from app.persistence.session import get_session_factory, set_tenant_context
from sqlalchemy import select


@dataclass(frozen=True)
class IngestionContext:
    """A short-lived connection and delegated credential for one provider call."""

    connection: SourceConnection
    credential: DelegatedCredential


def validate_message(message: dict[str, object]) -> None:
    """Reject unsupported Celery payload versions before provider work.

    Messages created by the original fetch task omitted the version field.
    Treat those already-queued messages as version 1 so a deployment can drain
    them safely while all newly published messages carry the field explicitly.
    """
    version = message.get("schemaVersion")
    if version is None:
        return
    if version != 1:
        raise ValueError("INGESTION_SCHEMA_UNSUPPORTED")


def connector_for(source: Source) -> Connector:
    """Return the provider adapter for a source enum."""
    return {
        Source.CONFLUENCE: ConfluenceConnector(),
        Source.JIRA: JiraConnector(),
        Source.SLACK: SlackConnector(),
        Source.GOOGLE_DRIVE: GoogleDriveConnector(),
    }[source]


async def load_ingestion_context(
    tenant_id: str,
    connection_id: str,
    user_id: str | None = None,
    *,
    verify_atlassian: bool = False,
) -> IngestionContext:
    """Load one connected source and its latest user-scoped credential.

    Credentials are decrypted only in worker memory. Refreshes are persisted in
    a separate short transaction, never in a Celery message or provider log.
    """
    tenant_uuid = UUID(tenant_id)
    connection_uuid = UUID(connection_id)
    user_uuid = UUID(user_id) if user_id else None
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_uuid)
        connection_row = await session.scalar(
            select(SourceConnectionRow).where(
                SourceConnectionRow.id == connection_uuid,
                SourceConnectionRow.tenant_id == tenant_uuid,
                SourceConnectionRow.status == "connected",
            )
        )
        if connection_row is None:
            raise ValueError("SOURCE_CONNECTION_UNAVAILABLE")

        credential_row = await session.scalar(
            select(EncryptedCredential)
            .where(
                EncryptedCredential.source_connection_id == connection_uuid,
                EncryptedCredential.tenant_id == tenant_uuid,
                EncryptedCredential.user_id.is_not(None),
                EncryptedCredential.credential_type != "service_account",
                *(
                    [EncryptedCredential.user_id == user_uuid]
                    if user_uuid is not None
                    else []
                ),
            )
            .order_by(EncryptedCredential.updated_at.desc())
        )
        if credential_row is None:
            raise ValueError("SOURCE_IDENTITY_MISSING")

        identity = await session.scalar(
            select(IdentityLink).where(
                IdentityLink.source_connection_id == connection_uuid,
                IdentityLink.user_id == credential_row.user_id,
                IdentityLink.status == "verified",
            )
        )
        source = Source(str(connection_row.source))
        plaintext = get_credential_manager().decrypt(
            EncryptedPayload(
                ciphertext=credential_row.ciphertext,
                key_version=credential_row.key_version,
            )
        )
        token_payload = unpack_tokens(plaintext)
        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        if (
            credential_row.credential_type == "oauth_refresh"
            and token_payload.get("refresh_token") is None
            and not plaintext.lstrip().startswith("{")
        ):
            # Rows written before structured token storage contain only the
            # encrypted refresh token. Preserve them without treating it as
            # an access bearer.
            access_token = None
            refresh_token = plaintext
        expires_at = _parse_expiry(token_payload.get("expires_at")) or credential_row.expires_at
        credential_id = credential_row.id
        provider_subject = identity.provider_subject_id if identity else None
        connection = SourceConnection(
            id=UUID(str(connection_row.id)),
            tenant_id=UUID(str(connection_row.tenant_id)),
            source=source,
            external_workspace_id=connection_row.external_workspace_id,
            display_name=connection_row.display_name,
            status=SourceConnectionStatus(str(connection_row.status)),
            last_sync_at=connection_row.last_sync_at,
            last_reconcile_at=connection_row.last_reconcile_at,
        )

    refreshed = False
    if (
        _needs_refresh(expires_at)
        or (expires_at is None and isinstance(refresh_token, str) and bool(refresh_token))
        or not isinstance(access_token, str)
        or not access_token
    ):
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ValueError("SOURCE_CREDENTIAL_EXPIRED")
        refreshed_payload = await refresh_access_token(source, refresh_token)
        access_token = refreshed_payload["access_token"]
        refresh_token = refreshed_payload.get("refresh_token") or refresh_token
        expires_at = refreshed_payload.get("expires_at")
        refreshed = True

    cloud_id = connection.external_workspace_id
    if source in {Source.CONFLUENCE, Source.JIRA} and (
        verify_atlassian or cloud_id == "atlassian-workspace"
    ):
        try:
            resources = await get_atlassian_accessible_resources(access_token)
        except ValueError as exc:
            if str(exc) != "SOURCE_CREDENTIAL_EXPIRED" or not isinstance(refresh_token, str):
                raise
            refreshed_payload = await refresh_access_token(source, refresh_token)
            access_token = refreshed_payload["access_token"]
            refresh_token = refreshed_payload.get("refresh_token") or refresh_token
            expires_at = refreshed_payload.get("expires_at")
            refreshed = True
            resources = await get_atlassian_accessible_resources(access_token)
        resource_ids = {str(resource["id"]) for resource in resources}
        if cloud_id not in resource_ids:
            cloud_id = str(resources[0]["id"])

    if refreshed:
        async with factory() as session:
            await set_tenant_context(session, tenant_uuid)
            row = await session.scalar(
                select(EncryptedCredential).where(
                    EncryptedCredential.id == credential_id,
                    EncryptedCredential.tenant_id == tenant_uuid,
                )
            )
            if row is not None:
                payload = get_credential_manager().encrypt(
                    pack_tokens(
                        access_token,
                        refresh_token=refresh_token,
                        expires_at=expires_at,
                    )
                )
                row.ciphertext = payload.ciphertext
                row.key_version = payload.key_version
                row.expires_at = expires_at
                await session.commit()

    if source in {Source.CONFLUENCE, Source.JIRA} and cloud_id != connection.external_workspace_id:
        async with factory() as session:
            await set_tenant_context(session, tenant_uuid)
            row = await session.scalar(
                select(SourceConnectionRow).where(
                    SourceConnectionRow.id == connection_uuid,
                    SourceConnectionRow.tenant_id == tenant_uuid,
                )
            )
            if row is not None:
                row.external_workspace_id = cloud_id
                await session.commit()

    metadata = {
        "workspace_id": cloud_id,
        "provider_subject_id": provider_subject,
    }
    if source in {Source.CONFLUENCE, Source.JIRA}:
        metadata["cloud_id"] = cloud_id
    return IngestionContext(
        connection=connection,
        credential=DelegatedCredential(
            source=source,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            provider_metadata=metadata,
        ),
    )


def _needs_refresh(expires_at: datetime | None) -> bool:
    return expires_at is not None and expires_at <= datetime.now(UTC) + timedelta(seconds=60)


def _parse_expiry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
