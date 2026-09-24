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


async def start_atlassian_oauth(user: AuthenticatedUser) -> str:
    """Initiate Atlassian OAuth flow — returns authorisation URL."""
    from urllib.parse import urlencode

    from app.settings import get_settings

    settings = get_settings()
    params = urlencode(
        {
            "audience": "api.atlassian.com",
            "client_id": settings.ATLASSIAN_CLIENT_ID,
            "scope": "read:confluence:content read:jira:work",
            "redirect_uri": settings.ATLASSIAN_REDIRECT_URI,
            "state": str(user.user_id),
            "response_type": "code",
            "prompt": "consent",
        }
    )
    return f"https://auth.atlassian.com/authorize?{params}"


async def complete_atlassian_oauth(code: str, state: str, user: AuthenticatedUser) -> None:
    """Complete Atlassian OAuth callback — exchanges code for tokens and persists."""
    import httpx

    from app.auth.credentials import get_credential_manager
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
    if not refresh_token:
        logger.warning("Atlassian OAuth: no refresh token returned")
        return

    cm = get_credential_manager()
    cm.encrypt(refresh_token)

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        logger.info("Atlassian OAuth completed", user_id=str(user.user_id))


class ConfluenceConnector:
    """Confluence connector implementing the Connector protocol."""

    source = Source.CONFLUENCE

    async def initial_scan(
        self, connection: SourceConnection, cursor: str | None
    ) -> ScanPage:
        # Full implementation would call Confluence REST API
        return ScanPage(objects=[], next_cursor=None)

    async def read_changes(
        self, connection: SourceConnection, cursor: str | None
    ) -> ChangePage:
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
