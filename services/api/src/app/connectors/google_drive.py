"""Google Drive connector — OAuth flows and connector implementation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.auth.oidc import AuthenticatedUser
from app.connectors.errors import PermanentError
from app.connectors.http import json_body, request
from app.connectors.parsing import next_cursor, strip_html
from app.domain.types import (
    AccessCheckResult,
    AccessOutcome,
    ChangePage,
    ConnectorOutcome,
    DelegatedCredential,
    FetchAclResult,
    FetchObjectResult,
    ScanPage,
    Source,
    SourceConnection,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)


async def start_google_oauth(user: AuthenticatedUser, oauth_state: str = "") -> str:
    """Initiate Google OAuth flow.

    The oauth_state is a short-lived signed binding passed through to the
    provider and back to the callback.
    """
    from urllib.parse import urlencode

    from app.settings import get_settings

    settings = get_settings()
    if not oauth_state:
        raise ValueError("AUTH_INVALID_TOKEN")
    state_val = oauth_state
    params = urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "scope": "https://www.googleapis.com/auth/drive.readonly",
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "state": state_val,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


async def complete_google_oauth(code: str, state: str, user: AuthenticatedUser) -> None:
    """Complete Google OAuth callback — exchanges code for tokens and persists."""
    import httpx
    from sqlalchemy import select as sa_select

    from app.auth.credentials import get_credential_manager
    from app.auth.identities import create_identity_link
    from app.connectors.oauth import pack_tokens
    from app.domain.types import CredentialType
    from app.persistence.models import EncryptedCredential, SourceConnection
    from app.persistence.session import get_session_factory, set_tenant_context
    from app.settings import get_settings

    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        token_data = response.json()

    refresh_token = token_data.get("refresh_token", "")
    access_token = token_data.get("access_token", "")
    if not refresh_token and not access_token:
        logger.warning("Google OAuth: no tokens returned")
        return

    # Determine provider subject from the userinfo API
    provider_subject = "google-user"
    workspace_id = "google-drive"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            if res.status_code == 200:
                me_data = res.json()
                provider_subject = str(me_data.get("id", provider_subject))
    except Exception:
        logger.debug("Could not fetch Google user identity", exc_info=True)

    cm = get_credential_manager()
    expires_at = None
    if isinstance(token_data.get("expires_in"), (int, float)):
        expires_at = datetime.now(UTC) + timedelta(seconds=int(token_data["expires_in"]))
    payload = cm.encrypt(
        pack_tokens(
            access_token,
            refresh_token=refresh_token or None,
            expires_at=expires_at,
        )
    )

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)

        # Find or create the source connection
        stmt = sa_select(SourceConnection).where(
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.source == "google_drive",
            SourceConnection.external_workspace_id == workspace_id,
        )
        result = await session.execute(stmt)
        connection = result.scalar_one_or_none()

        if connection is None:
            connection = SourceConnection(
                tenant_id=user.tenant_id,
                source="google_drive",
                external_workspace_id=workspace_id,
                display_name="Google Drive",
                status="connected",
            )
            session.add(connection)
            await session.flush()

        # Persist the encrypted credential
        credential = EncryptedCredential(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            source_connection_id=connection.id,
            credential_type=CredentialType.OAUTH_REFRESH.value,
            ciphertext=payload.ciphertext,
            key_version=payload.key_version,
            expires_at=expires_at,
        )
        session.add(credential)

        # Create identity link
        await create_identity_link(
            session,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            source_connection_id=connection.id,
            provider_subject_id=provider_subject,
            provider_account_type="google",
        )

        await session.commit()
        logger.info(
            "Google OAuth completed",
            user_id=str(user.user_id),
            provider_subject=provider_subject,
        )


class GoogleDriveConnector:
    """Google Drive connector implementing the Connector protocol."""

    source = Source.GOOGLE_DRIVE

    async def initial_scan(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ScanPage:
        credential = _require_credential(credential)
        response = await request(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            access_token=credential.access_token,
            params={
                "pageSize": 100,
                "pageToken": cursor or "",
                "q": "trashed = false",
                "spaces": "drive",
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "fields": (
                    "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,"
                    "parents,version,permissions,driveId)"
                ),
            },
        )
        payload = json_body(response)
        files = payload.get("files", [])
        return ScanPage(
            objects=[
                _scan_object(item)
                for item in files
                if isinstance(item, dict) and _is_indexable(item)
            ],
            next_cursor=next_cursor(payload),
        )

    async def read_changes(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ChangePage:
        credential = _require_credential(credential)
        page_token = cursor
        if not page_token:
            start = await request(
                "GET",
                "https://www.googleapis.com/drive/v3/changes/startPageToken",
                access_token=credential.access_token,
                params={"supportsAllDrives": "true"},
            )
            page_token = json_body(start).get("startPageToken")
        if not page_token:
            return ChangePage(changes=[], next_cursor=None)
        response = await request(
            "GET",
            "https://www.googleapis.com/drive/v3/changes",
            access_token=credential.access_token,
            params={
                "pageToken": page_token,
                "pageSize": 100,
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "includeCorpusRemovals": "true",
                "fields": (
                    "nextPageToken,newStartPageToken,changes(fileId,removed,file("
                    "id,name,mimeType,modifiedTime,webViewLink,parents,version,permissions,driveId))"
                ),
            },
        )
        payload = json_body(response)
        changes: list[dict[str, object]] = []
        for item in payload.get("changes", []):
            if not isinstance(item, dict):
                continue
            if item.get("removed") is True:
                changes.append(
                    {
                        "external_object_id": str(item.get("fileId")),
                        "change_type": "deleted",
                    }
                )
            elif isinstance(item.get("file"), dict) and _is_indexable(item["file"]):
                changes.append(_scan_object(item["file"]))
        next_value = payload.get("nextPageToken") or payload.get("newStartPageToken")
        return ChangePage(
            changes=changes,
            next_cursor=str(next_value) if next_value else None,
            has_deletions=any(item.get("change_type") == "deleted" for item in changes),
        )

    async def fetch_object(
        self,
        connection: SourceConnection,
        external_object_id: str,
        credential: DelegatedCredential | None = None,
    ) -> FetchObjectResult:
        credential = _require_credential(credential)
        response = await request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{external_object_id}",
            access_token=credential.access_token,
            params={
                "supportsAllDrives": "true",
                "fields": (
                    "id,name,mimeType,modifiedTime,webViewLink,parents,version,"
                    "trashed,permissions,driveId"
                ),
            },
        )
        payload = json_body(response)
        if payload.get("trashed") is True:
            return FetchObjectResult(
                outcome=ConnectorOutcome.NOT_FOUND,
                external_object_id=external_object_id,
            )
        mime_type = str(payload.get("mimeType") or "")
        content = await _content_for_file(credential, external_object_id, mime_type)
        return FetchObjectResult(
            outcome=ConnectorOutcome.SUCCESS,
            external_object_id=external_object_id,
            source_revision=str(
                payload.get("version") or payload.get("modifiedTime") or external_object_id
            ),
            object_type=mime_type or "file",
            parent_external_id=_first_parent(payload.get("parents")),
            canonical_url=str(payload.get("webViewLink")) if payload.get("webViewLink") else None,
            title=str(payload.get("name")) if payload.get("name") else external_object_id,
            content=content,
            last_modified_at=_parse_datetime(payload.get("modifiedTime")),
            permission_principals=_permissions(payload.get("permissions")),
            provider_metadata={"drive_id": payload.get("driveId")},
        )

    async def fetch_acl(
        self,
        connection: SourceConnection,
        external_object_id: str,
        credential: DelegatedCredential | None = None,
    ) -> FetchAclResult:
        credential = _require_credential(credential)
        permissions = await _permissions_for_file(credential, external_object_id)
        return FetchAclResult(outcome=ConnectorOutcome.SUCCESS, grants=permissions)

    async def check_access(  # noqa: PLR0911
        self, user_credential: DelegatedCredential, external_object_id: str
    ) -> AccessCheckResult:
        """Authoritatively check Drive access with the delegated user's token."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{external_object_id}",
                    headers={"Authorization": f"Bearer {user_credential.access_token}"},
                    params={"fields": "id,name,trashed"},
                )
        except httpx.TimeoutException:
            return AccessCheckResult(outcome=AccessOutcome.TIMEOUT, reason_code="provider_timeout")
        except httpx.HTTPError:
            return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="provider_error")

        request_id = response.headers.get("x-guploader-uploadid") or response.headers.get(
            "x-request-id"
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("trashed") is True:
                return AccessCheckResult(
                    outcome=AccessOutcome.DENY,
                    reason_code="object_trashed",
                    provider_request_id=request_id,
                )
            return AccessCheckResult(
                outcome=AccessOutcome.ALLOW,
                reason_code="provider_allow",
                provider_request_id=request_id,
            )
        if response.status_code in {401, 403}:
            return AccessCheckResult(
                outcome=(
                    AccessOutcome.EXPIRED_CREDENTIAL
                    if response.status_code == 401
                    else AccessOutcome.DENY
                ),
                reason_code=(
                    "credential_expired"
                    if response.status_code == 401
                    else "provider_deny"
                ),
                provider_request_id=request_id,
            )
        if response.status_code == 404:
            return AccessCheckResult(
                outcome=AccessOutcome.DENY,
                reason_code="object_not_found_or_forbidden",
                provider_request_id=request_id,
            )
        if response.status_code == 429:
            return AccessCheckResult(
                outcome=AccessOutcome.TIMEOUT,
                reason_code="provider_rate_limited",
                provider_request_id=request_id,
            )
        return AccessCheckResult(
            outcome=AccessOutcome.UNKNOWN,
            reason_code=f"provider_status_{response.status_code}",
            provider_request_id=request_id,
        )


def _require_credential(credential: DelegatedCredential | None) -> DelegatedCredential:
    if credential is None or not credential.access_token:
        raise ValueError("SOURCE_CREDENTIAL_EXPIRED")
    return credential


def _scan_object(item: dict[str, object]) -> dict[str, object]:
    return {
        "external_object_id": str(item.get("id")),
        "object_type": str(item.get("mimeType") or "file"),
        "parent_external_id": _first_parent(item.get("parents")),
        "canonical_url": str(item.get("webViewLink")) if item.get("webViewLink") else None,
        "title": str(item.get("name")) if item.get("name") else None,
        "source_revision": str(item.get("version") or item.get("modifiedTime") or item.get("id")),
        "provider_metadata": {"drive_id": item.get("driveId")},
    }


def _is_indexable(item: dict[str, object]) -> bool:
    return item.get("mimeType") != "application/vnd.google-apps.folder"


def _first_parent(value: object) -> str | None:
    return str(value[0]) if isinstance(value, list) and value else None


def _permissions(value: object) -> list[dict[str, object]]:
    grants: list[dict[str, object]] = []
    if not isinstance(value, list):
        return grants
    for permission in value:
        if not isinstance(permission, dict) or not permission.get("id"):
            continue
        role = str(permission.get("role") or "reader")
        if role not in {"reader", "commenter", "writer", "owner"}:
            continue
        grants.append(
            {
                "principal_type": str(permission.get("type") or "user"),
                "provider_principal_id": str(permission["id"]),
                "display_name": permission.get("displayName") or permission.get("emailAddress"),
                "permission": "read",
                "grant_effect": "allow",
                "inheritance_path": {
                    "permission_details": permission.get("permissionDetails", [])
                },
            }
        )
    return grants


async def _permissions_for_file(
    credential: DelegatedCredential,
    external_object_id: str,
) -> list[dict[str, object]]:
    grants: list[dict[str, object]] = []
    page_token: str | None = None
    for _ in range(10):
        response = await request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{external_object_id}/permissions",
            access_token=credential.access_token,
            params={
                "supportsAllDrives": "true",
                "pageSize": 100,
                "pageToken": page_token or "",
                "fields": (
                    "nextPageToken,permissions(id,type,role,displayName,emailAddress,"
                    "permissionDetails)"
                ),
            },
        )
        payload = json_body(response)
        grants.extend(_permissions(payload.get("permissions")))
        next_value = payload.get("nextPageToken")
        page_token = str(next_value) if next_value else None
        if page_token is None:
            break
    return grants


async def _content_for_file(
    credential: DelegatedCredential,
    external_object_id: str,
    mime_type: str,
) -> str:
    export_types = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.presentation": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
    }
    if mime_type in export_types:
        response = await request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{external_object_id}/export",
            access_token=credential.access_token,
            params={"mimeType": export_types[mime_type]},
        )
        return strip_html(response.text)
    if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
        response = await request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{external_object_id}",
            access_token=credential.access_token,
            params={"alt": "media"},
        )
        return strip_html(response.text)
    raise PermanentError("Unsupported Drive file type")


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
