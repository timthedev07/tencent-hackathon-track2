"""Confluence connector — OAuth flows and connector implementation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.auth.oidc import AuthenticatedUser
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


async def start_atlassian_oauth(user: AuthenticatedUser, oauth_state: str = "") -> str:
    """Initiate Atlassian OAuth flow — returns authorisation URL.

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
            "audience": "api.atlassian.com",
            "client_id": settings.ATLASSIAN_CLIENT_ID,
            "scope": (
                "read:confluence-content.read read:confluence-content.all "
                "read:confluence-space.summary read:jira-work read:me offline_access"
            ),
            "redirect_uri": settings.ATLASSIAN_REDIRECT_URI,
            "state": state_val,
            "response_type": "code",
            "prompt": "consent",
        }
    )
    return f"https://auth.atlassian.com/authorize?{params}"


async def complete_atlassian_oauth(code: str, state: str, user: AuthenticatedUser) -> None:
    """Complete Atlassian OAuth callback — exchanges code for tokens and persists."""
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
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": settings.ATLASSIAN_CLIENT_ID,
                "client_secret": settings.ATLASSIAN_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.ATLASSIAN_REDIRECT_URI,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        token_data = response.json()

    refresh_token = token_data.get("refresh_token", "")
    access_token = token_data.get("access_token", "")
    if not refresh_token and not access_token:
        logger.warning("Atlassian OAuth: no tokens returned")
        return

    # Determine provider subject and cloud id from Atlassian APIs.
    provider_subject: str | None = None
    workspace_id = "atlassian-workspace"
    workspace_name = "Atlassian"
    try:
        async with httpx.AsyncClient() as client:
            me_res = await client.get(
                "https://api.atlassian.com/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            if me_res.status_code == 200:
                me_data = me_res.json()
                account_id = me_data.get("account_id")
                if account_id:
                    provider_subject = str(account_id)
            resources_res = await client.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            if resources_res.status_code == 200 and resources_res.json():
                resource = resources_res.json()[0]
                workspace_id = str(resource.get("id", workspace_id))
                workspace_name = str(resource.get("name", workspace_name))
    except Exception:
        logger.debug("Could not fetch Atlassian account/resource metadata", exc_info=True)

    if not provider_subject:
        raise ValueError("ATLASSIAN_IDENTITY_UNAVAILABLE")

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

        for source_name in ("confluence", "jira"):
            stmt = sa_select(SourceConnection).where(
                SourceConnection.tenant_id == user.tenant_id,
                SourceConnection.source == source_name,
                SourceConnection.external_workspace_id == workspace_id,
            )
            result = await session.execute(stmt)
            connection = result.scalar_one_or_none()

            if connection is None:
                connection = SourceConnection(
                    tenant_id=user.tenant_id,
                    source=source_name,
                    external_workspace_id=workspace_id,
                    display_name=workspace_name,
                    status="connected",
                )
                session.add(connection)
                await session.flush()

            credential = EncryptedCredential(
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                source_connection_id=connection.id,
                credential_type=(
                    CredentialType.OAUTH_REFRESH.value
                    if refresh_token
                    else CredentialType.OAUTH_ACCESS.value
                ),
                ciphertext=payload.ciphertext,
                key_version=payload.key_version,
                expires_at=expires_at,
            )
            session.add(credential)

            await create_identity_link(
                session,
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                source_connection_id=connection.id,
                provider_subject_id=provider_subject,
                provider_account_type="atlassian",
            )

        await session.commit()
        logger.info(
            "Atlassian OAuth completed",
            user_id=str(user.user_id),
            provider_subject=provider_subject,
        )


class ConfluenceConnector:
    """Confluence connector implementing the Connector protocol."""

    source = Source.CONFLUENCE

    async def initial_scan(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ScanPage:
        credential = _require_credential(credential)
        cloud_id = _cloud_id(connection, credential)
        url = _page_url(cloud_id, cursor)
        params = (
            None
            if cursor and cursor.startswith("http")
            else {"limit": 100, "status": "current"}
        )
        response = await request(
            "GET",
            url,
            access_token=credential.access_token,
            params=params,
        )
        payload = json_body(response)
        objects = [
            _scan_object(item)
            for item in payload.get("results", [])
            if isinstance(item, dict)
        ]
        return ScanPage(objects=objects, next_cursor=next_cursor(payload))

    async def read_changes(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ChangePage:
        page = await self.initial_scan(connection, cursor, credential)
        return ChangePage(changes=page.objects, next_cursor=page.next_cursor)

    async def fetch_object(
        self,
        connection: SourceConnection,
        external_object_id: str,
        credential: DelegatedCredential | None = None,
    ) -> FetchObjectResult:
        credential = _require_credential(credential)
        cloud_id = _cloud_id(connection, credential)
        response = await request(
            "GET",
            f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{external_object_id}",
            access_token=credential.access_token,
            params={"body-format": "storage"},
        )
        payload = json_body(response)
        version = payload.get("version") if isinstance(payload.get("version"), dict) else {}
        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        storage = body.get("storage") if isinstance(body.get("storage"), dict) else {}
        content = strip_html(str(storage.get("value") or ""))
        links = payload.get("_links") if isinstance(payload.get("_links"), dict) else {}
        canonical_url = links.get("webui")
        if canonical_url and links.get("base"):
            canonical_url = f"{links['base']}{canonical_url}"
        revision = str(version.get("number") or version.get("createdAt") or payload.get("id"))
        return FetchObjectResult(
            outcome=ConnectorOutcome.SUCCESS,
            external_object_id=external_object_id,
            source_revision=revision,
            object_type="page",
            parent_external_id=str(payload.get("parentId")) if payload.get("parentId") else None,
            canonical_url=str(canonical_url) if canonical_url else None,
            title=str(payload.get("title")) if payload.get("title") else None,
            content=content,
            last_modified_at=version.get("createdAt"),
            provider_metadata={
                "space_id": payload.get("spaceId"),
                "ancestor_ids": _ancestor_ids(payload.get("ancestors")),
            },
        )

    async def fetch_acl(
        self,
        connection: SourceConnection,
        external_object_id: str,
        credential: DelegatedCredential | None = None,
    ) -> FetchAclResult:
        credential = _require_credential(credential)
        cloud_id = _cloud_id(connection, credential)
        response = await request(
            "GET",
            f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/content/{external_object_id}/restriction",
            access_token=credential.access_token,
            params={"expand": "restrictions.user,restrictions.group", "limit": 100},
        )
        payload = json_body(response)
        grants: list[dict[str, object]] = []
        operation_results = payload.get("results", [])
        for item in operation_results:
            if not isinstance(item, dict):
                continue
            operation = str(item.get("operation") or "read")
            if operation not in {"read", "view"}:
                continue
            restrictions = item.get("restrictions")
            if not isinstance(restrictions, dict):
                continue
            for principal in _restriction_principals(restrictions.get("user"), "user"):
                grants.append(
                    {
                        "principal_type": principal["principal_type"],
                        "provider_principal_id": principal["provider_principal_id"],
                        "permission": "read",
                        "grant_effect": "allow",
                        "inheritance_path": {"operation": operation},
                    }
                )
            for principal in _restriction_principals(restrictions.get("group"), "group"):
                grants.append(
                    {
                        "principal_type": principal["principal_type"],
                        "provider_principal_id": principal["provider_principal_id"],
                        "permission": "read",
                        "grant_effect": "allow",
                        "inheritance_path": {"operation": operation},
                    }
                )
        return FetchAclResult(outcome=ConnectorOutcome.SUCCESS, grants=grants)

    async def check_access(  # noqa: PLR0911
        self, user_credential: DelegatedCredential, external_object_id: str
    ) -> AccessCheckResult:
        """Authoritatively check Confluence page visibility with a delegated token."""
        import httpx

        cloud_id = user_credential.provider_metadata.get("cloud_id")
        if not cloud_id:
            return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="cloud_id_missing")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{external_object_id}",
                    headers={"Authorization": f"Bearer {user_credential.access_token}"},
                    params={"body-format": "none"},
                )
        except httpx.TimeoutException:
            return AccessCheckResult(outcome=AccessOutcome.TIMEOUT, reason_code="provider_timeout")
        except httpx.HTTPError:
            return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="provider_error")

        request_id = response.headers.get("x-arequestid") or response.headers.get("x-request-id")
        if response.status_code == 200:
            return AccessCheckResult(
                outcome=AccessOutcome.ALLOW,
                reason_code="provider_allow",
                provider_request_id=request_id,
            )
        if response.status_code == 401:
            return AccessCheckResult(
                outcome=AccessOutcome.EXPIRED_CREDENTIAL,
                reason_code="credential_expired",
                provider_request_id=request_id,
            )
        if response.status_code in {403, 404}:
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


def _cloud_id(
    connection: SourceConnection,
    credential: DelegatedCredential,
) -> str:
    return str(credential.provider_metadata.get("cloud_id") or connection.external_workspace_id)


def _page_url(cloud_id: str, cursor: str | None) -> str:
    if cursor and cursor.startswith("http"):
        return cursor
    return f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages"


def _scan_object(item: dict[str, object]) -> dict[str, object]:
    version = item.get("version") if isinstance(item.get("version"), dict) else {}
    links = item.get("_links") if isinstance(item.get("_links"), dict) else {}
    canonical_url = links.get("webui")
    if canonical_url and links.get("base"):
        canonical_url = f"{links['base']}{canonical_url}"
    return {
        "external_object_id": str(item.get("id")),
        "object_type": "page",
        "parent_external_id": str(item.get("parentId")) if item.get("parentId") else None,
        "canonical_url": str(canonical_url) if canonical_url else None,
        "title": str(item.get("title")) if item.get("title") else None,
        "source_revision": str(
            version.get("number") or version.get("createdAt") or item.get("id")
        ),
        "provider_metadata": {
            "space_id": item.get("spaceId"),
            "ancestor_ids": _ancestor_ids(item.get("ancestors")),
        },
    }


def _ancestor_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item["id"]) for item in value if isinstance(item, dict) and item.get("id")]


def _restriction_principals(value: object, principal_type: str) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return []
    results = value.get("results")
    if not isinstance(results, list):
        return []
    principals: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        identifier = item.get("accountId") or item.get("id") or item.get("name")
        if identifier:
            principals.append(
                {
                    "principal_type": principal_type,
                    "provider_principal_id": str(identifier),
                }
            )
    return principals
