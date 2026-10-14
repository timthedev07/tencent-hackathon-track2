"""Google Drive connector — OAuth flows and connector implementation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.auth.oidc import AuthenticatedUser
from app.connectors.errors import ConnectorError, PermanentError
from app.connectors.http import json_body, request
from app.connectors.parsing import object_list, strip_html
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

_EXPORT_MIME_TYPES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.presentation",
        "application/vnd.google-apps.spreadsheet",
    }
)
_DIRECT_TEXT_MIME_TYPES = frozenset({"application/json", "application/xml"})
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_SCOPE_ROOT_IDS_KEY = "allowed_root_folder_ids"
_SCAN_STATE_VERSION = 1
_SCAN_PAGE_SIZE = 100


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
            # Request OpenID identity scopes as well as Drive access. The
            # stable `sub` returned by Google's UserInfo endpoint is required
            # to keep delegated accounts separate between Relay users.
            "scope": (
                "openid email profile "
                "https://www.googleapis.com/auth/drive.readonly"
            ),
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
        raise ValueError("GOOGLE_IDENTITY_UNAVAILABLE")

    # Determine the immutable provider subject from Google's OpenID UserInfo
    # endpoint. Never fall back to a shared placeholder: that would make every
    # Google account appear to be the same identity.
    workspace_id = "google-drive"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            res.raise_for_status()
            me_data = res.json()
            provider_subject = str(me_data.get("sub", ""))
    except Exception as exc:
        logger.warning("Google OAuth identity lookup failed", error=str(exc))
        raise ValueError("GOOGLE_IDENTITY_UNAVAILABLE") from exc

    if not provider_subject:
        logger.warning("Google OAuth: UserInfo response did not contain a subject")
        raise ValueError("GOOGLE_IDENTITY_UNAVAILABLE")

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
        roots = _scope_root_ids(connection)
        if not roots:
            logger.warning(
                "Google Drive scan refused because no folder scope is configured",
                connection_id=str(connection.id),
            )
            return ScanPage(objects=[], next_cursor=None)

        state = _decode_scan_state(cursor, roots)
        while state["folder_index"] < len(state["folders"]):
            folder_id = state["folders"][state["folder_index"]]
            response = await request(
                "GET",
                "https://www.googleapis.com/drive/v3/files",
                access_token=credential.access_token,
                params=_list_params(
                    q=_parent_query(folder_id),
                    page_token=state.get("page_token"),
                ),
            )
            payload = json_body(response)
            files = [item for item in object_list(payload.get("files")) if isinstance(item, dict)]
            objects: list[dict[str, object]] = []
            for item in files:
                item_id = str(item.get("id") or "")
                if not item_id:
                    continue
                if _is_folder(item):
                    _add_child_folder(
                        state,
                        item_id,
                        state["folder_roots"].get(folder_id, roots),
                    )
                elif _is_indexable(item):
                    objects.append(
                        _scan_object(item, state["folder_roots"].get(folder_id, roots))
                    )

            page_token = payload.get("nextPageToken")
            if page_token:
                state["page_token"] = str(page_token)
                return ScanPage(objects=objects, next_cursor=_encode_scan_state(state))

            state["folder_index"] += 1
            state["page_token"] = None
            if objects:
                next_value = (
                    _encode_scan_state(state)
                    if state["folder_index"] < len(state["folders"])
                    else None
                )
                return ScanPage(objects=objects, next_cursor=next_value)

        return ScanPage(objects=[], next_cursor=None)

    async def read_changes(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ChangePage:
        credential = _require_credential(credential)
        roots = _scope_root_ids(connection)
        if not roots:
            logger.warning(
                "Google Drive changes ignored because no folder scope is configured",
                connection_id=str(connection.id),
            )
            return ChangePage(changes=[], next_cursor=cursor)

        folder_roots = await _discover_scoped_folders(credential, roots)
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
        for item in object_list(payload.get("changes")):
            if not isinstance(item, dict):
                continue
            if item.get("removed") is True:
                changes.append(
                    {
                        "external_object_id": str(item.get("fileId")),
                        "change_type": "deleted",
                    }
                )
            elif isinstance(item.get("file"), dict):
                file = item["file"]
                file_id = str(file.get("id") or item.get("fileId") or "")
                parents = _parent_ids(file.get("parents"))
                if _is_indexable(file) and set(parents) & set(folder_roots):
                    roots_for_file = sorted(
                        root
                        for parent in parents
                        for root in folder_roots.get(parent, [])
                    )
                    changes.append(_scan_object(file, roots_for_file or roots))
                elif file_id:
                    # A moved-out or no-longer-supported object must stop being
                    # queryable even though Drive did not report a deletion.
                    changes.append(
                        {
                            "external_object_id": file_id,
                            "change_type": "deleted",
                        }
                    )
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

    async def folder_names(
        self,
        folder_ids: list[str],
        credential: DelegatedCredential | None = None,
    ) -> dict[str, str]:
        """Return visible names for folder IDs without exposing folder contents."""
        credential = _require_credential(credential)
        names: dict[str, str] = {}
        for folder_id in folder_ids:
            try:
                response = await request(
                    "GET",
                    f"https://www.googleapis.com/drive/v3/files/{folder_id}",
                    access_token=credential.access_token,
                    params={
                        "supportsAllDrives": "true",
                        "fields": "id,name,mimeType,trashed",
                    },
                )
                payload = json_body(response)
            except ConnectorError:
                continue
            if (
                payload.get("trashed") is not True
                and payload.get("mimeType") == _FOLDER_MIME_TYPE
                and isinstance(payload.get("name"), str)
                and payload["name"]
            ):
                names[folder_id] = payload["name"]
        return names

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
        if response.status_code == HTTPStatus.OK:
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
                    if response.status_code == HTTPStatus.UNAUTHORIZED
                    else AccessOutcome.DENY
                ),
                reason_code=(
                    "credential_expired"
                    if response.status_code == HTTPStatus.UNAUTHORIZED
                    else "provider_deny"
                ),
                provider_request_id=request_id,
            )
        if response.status_code == HTTPStatus.NOT_FOUND:
            return AccessCheckResult(
                outcome=AccessOutcome.DENY,
                reason_code="object_not_found_or_forbidden",
                provider_request_id=request_id,
            )
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
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


def _scope_root_ids(connection: SourceConnection) -> list[str]:
    configured = connection.scope_config.get(_SCOPE_ROOT_IDS_KEY, [])
    if not isinstance(configured, list):
        return []
    return sorted({value for value in configured if isinstance(value, str) and value})


def parse_folder_id(value: str) -> str:
    """Extract a Google Drive folder ID from a Drive link or raw ID."""
    candidate = value.strip()
    if not candidate:
        raise ValueError("Google Drive folder links must not be empty.")
    if "://" not in candidate:
        folder_id = candidate
    else:
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or parsed.netloc.lower() not in {
            "drive.google.com",
            "www.drive.google.com",
        }:
            raise ValueError("Use a secure Google Drive folder link.")
        path_parts = [part for part in parsed.path.split("/") if part]
        folder_id = ""
        if "folders" in path_parts:
            folder_index = path_parts.index("folders")
            if folder_index + 1 < len(path_parts):
                folder_id = path_parts[folder_index + 1]
        if not folder_id:
            folder_id = parse_qs(parsed.query).get("id", [""])[0]
    if not folder_id or "'" in folder_id or any(char.isspace() for char in folder_id):
        raise ValueError("The Google Drive folder link does not contain a valid folder ID.")
    return folder_id


def _list_params(*, q: str, page_token: str | None = None) -> dict[str, object]:
    params: dict[str, object] = {
        "pageSize": _SCAN_PAGE_SIZE,
        "q": q,
        "spaces": "drive",
        "includeItemsFromAllDrives": "true",
        "supportsAllDrives": "true",
        "fields": (
            "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,"
            "parents,version,permissions,driveId)"
        ),
    }
    if page_token:
        params["pageToken"] = page_token
    return params


def _parent_query(folder_id: str) -> str:
    # Drive IDs cannot contain a single quote, but escaping keeps this helper
    # safe if provider identifiers ever change shape.
    escaped = folder_id.replace("'", "\\'")
    return f"'{escaped}' in parents and trashed = false"


def _decode_scan_state(cursor: str | None, roots: list[str]) -> dict[str, Any]:
    if not cursor:
        return {
            "version": _SCAN_STATE_VERSION,
            "folders": list(roots),
            "folder_index": 0,
            "page_token": None,
            "seen_folders": list(roots),
            "folder_roots": {root: [root] for root in roots},
        }
    try:
        value = json.loads(cursor)
    except (TypeError, json.JSONDecodeError):
        # Cursors created by the old unscoped scanner were plain Drive page
        # tokens. Restart safely instead of interpreting them as a folder.
        return _decode_scan_state(None, roots)
    if not isinstance(value, dict) or value.get("version") != _SCAN_STATE_VERSION:
        return _decode_scan_state(None, roots)
    folders = value.get("folders")
    folder_index = value.get("folder_index")
    seen_folders = value.get("seen_folders")
    folder_roots = value.get("folder_roots")
    if (
        not isinstance(folders, list)
        or not all(isinstance(item, str) and item for item in folders)
        or not isinstance(folder_index, int)
        or folder_index < 0
        or not isinstance(seen_folders, list)
        or not isinstance(folder_roots, dict)
    ):
        return _decode_scan_state(None, roots)
    for root in roots:
        if root not in folders:
            folders.insert(0, root)
        if root not in seen_folders:
            seen_folders.append(root)
        folder_roots.setdefault(root, [root])
    return {
        "version": _SCAN_STATE_VERSION,
        "folders": folders,
        "folder_index": min(folder_index, len(folders)),
        "page_token": value.get("page_token") if isinstance(value.get("page_token"), str) else None,
        "seen_folders": seen_folders,
        "folder_roots": {
            str(folder): [str(root) for root in roots_for_folder if isinstance(root, str)]
            for folder, roots_for_folder in folder_roots.items()
            if isinstance(roots_for_folder, list)
        },
    }


def _encode_scan_state(state: dict[str, Any]) -> str:
    return json.dumps(state, separators=(",", ":"), sort_keys=True)


def _add_child_folder(
    state: dict[str, Any], folder_id: str, root_ids: list[str]
) -> None:
    seen_folders = state["seen_folders"]
    folder_roots = state["folder_roots"]
    if folder_id not in seen_folders:
        seen_folders.append(folder_id)
        state["folders"].append(folder_id)
    existing = set(folder_roots.get(folder_id, []))
    folder_roots[folder_id] = sorted(existing | set(root_ids))


async def _discover_scoped_folders(
    credential: DelegatedCredential,
    roots: list[str],
) -> dict[str, list[str]]:
    """Return every folder beneath the configured roots and its root ancestry."""
    folder_roots: dict[str, list[str]] = {root: [root] for root in roots}
    queue = list(roots)
    seen = set(roots)
    while queue:
        parent_id = queue.pop(0)
        response = await request(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            access_token=credential.access_token,
            params=_list_params(q=_parent_query(parent_id)),
        )
        payload = json_body(response)
        for item in object_list(payload.get("files")):
            if not isinstance(item, dict) or not _is_folder(item):
                continue
            child_id = str(item.get("id") or "")
            if not child_id:
                continue
            child_roots = folder_roots.get(parent_id, roots)
            folder_roots[child_id] = sorted(
                set(folder_roots.get(child_id, [])) | set(child_roots)
            )
            if child_id not in seen:
                seen.add(child_id)
                queue.append(child_id)
        next_page = payload.get("nextPageToken")
        while next_page:
            response = await request(
                "GET",
                "https://www.googleapis.com/drive/v3/files",
                access_token=credential.access_token,
                params=_list_params(q=_parent_query(parent_id), page_token=str(next_page)),
            )
            payload = json_body(response)
            for item in object_list(payload.get("files")):
                if not isinstance(item, dict) or not _is_folder(item):
                    continue
                child_id = str(item.get("id") or "")
                if not child_id:
                    continue
                child_roots = folder_roots.get(parent_id, roots)
                folder_roots[child_id] = sorted(
                    set(folder_roots.get(child_id, [])) | set(child_roots)
                )
                if child_id not in seen:
                    seen.add(child_id)
                    queue.append(child_id)
            next_page = payload.get("nextPageToken")
    return folder_roots


def _is_folder(item: dict[str, object]) -> bool:
    return str(item.get("mimeType") or "") == _FOLDER_MIME_TYPE


def _parent_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(parent) for parent in value if isinstance(parent, str) and parent]


def _scan_object(
    item: dict[str, object], scope_root_ids: list[str] | None = None
) -> dict[str, object]:
    return {
        "external_object_id": str(item.get("id")),
        "object_type": str(item.get("mimeType") or "file"),
        "parent_external_id": _first_parent(item.get("parents")),
        "canonical_url": str(item.get("webViewLink")) if item.get("webViewLink") else None,
        "title": str(item.get("name")) if item.get("name") else None,
        "source_revision": str(item.get("version") or item.get("modifiedTime") or item.get("id")),
        "provider_metadata": {
            "drive_id": item.get("driveId"),
            "parent_ids": _parent_ids(item.get("parents")),
            "scope_root_ids": sorted(set(scope_root_ids or [])),
        },
    }


def _is_indexable(item: dict[str, object]) -> bool:
    mime_type = str(item.get("mimeType") or "")
    return (
        mime_type in _EXPORT_MIME_TYPES
        or mime_type in _DIRECT_TEXT_MIME_TYPES
        or mime_type.startswith("text/")
    )


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
    response = await request(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{external_object_id}",
        access_token=credential.access_token,
        params={
            "supportsAllDrives": "true",
            "fields": "permissions(id,type,role,displayName,emailAddress,permissionDetails)",
        },
    )
    return _permissions(json_body(response).get("permissions"))


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
    if mime_type.startswith("text/") or mime_type in _DIRECT_TEXT_MIME_TYPES:
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
