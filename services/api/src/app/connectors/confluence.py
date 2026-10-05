"""Confluence connector — OAuth flows and connector implementation."""

from __future__ import annotations

from app.auth.oidc import AuthenticatedUser
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

    The oauth_state is passed through to the provider and back to the callback.
    In dev mode this carries the dev token so the callback can authenticate.
    """
    from urllib.parse import urlencode

    from app.settings import get_settings

    settings = get_settings()
    state_val = oauth_state or str(user.user_id)
    params = urlencode(
        {
            "audience": "api.atlassian.com",
            "client_id": settings.ATLASSIAN_CLIENT_ID,
            "scope": "read:confluence-content.read read:confluence-space.summary read:jira-work",
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

    # Determine provider subject from the accessible resources API
    provider_subject = "atlassian-user"
    workspace_id = "atlassian-workspace"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                "https://api.atlassian.com/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            if res.status_code == 200:
                me_data = res.json()
                provider_subject = str(me_data.get("account_id", provider_subject))
    except Exception:
        logger.debug("Could not fetch Atlassian user identity", exc_info=True)

    # Encrypt the refresh token (or access token as fallback)
    cm = get_credential_manager()
    token_to_store = refresh_token or access_token
    payload = cm.encrypt(token_to_store)

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)

        # Find or create the source connection
        stmt = sa_select(SourceConnection).where(
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.source == "confluence",
            SourceConnection.external_workspace_id == workspace_id,
        )
        result = await session.execute(stmt)
        connection = result.scalar_one_or_none()

        if connection is None:
            connection = SourceConnection(
                tenant_id=user.tenant_id,
                source="confluence",
                external_workspace_id=workspace_id,
                display_name="Atlassian",
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
        )
        session.add(credential)

        # Create identity link
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

    async def initial_scan(self, connection: SourceConnection, cursor: str | None) -> ScanPage:
        # Full implementation would call Confluence REST API
        return ScanPage(objects=[], next_cursor=None)

    async def read_changes(self, connection: SourceConnection, cursor: str | None) -> ChangePage:
        return ChangePage(changes=[], next_cursor=None)

    async def fetch_object(
        self, connection: SourceConnection, external_object_id: str
    ) -> FetchObjectResult:
        return FetchObjectResult(
            outcome=ConnectorOutcome.NOT_FOUND, external_object_id=external_object_id
        )

    async def fetch_acl(
        self, connection: SourceConnection, external_object_id: str
    ) -> FetchAclResult:
        return FetchAclResult(outcome=ConnectorOutcome.SUCCESS)

    async def check_access(
        self, user_credential: DelegatedCredential, external_object_id: str
    ) -> AccessCheckResult:
        return AccessCheckResult(outcome=AccessOutcome.ALLOW)
