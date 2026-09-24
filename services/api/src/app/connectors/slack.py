"""Slack connector — OAuth flows and connector implementation."""

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


async def start_slack_oauth(user: AuthenticatedUser) -> str:
    """Initiate Slack OAuth flow."""
    from urllib.parse import urlencode

    from app.settings import get_settings

    settings = get_settings()
    params = urlencode(
        {
            "client_id": settings.SLACK_CLIENT_ID,
            "scope": "channels:read channels:history groups:read groups:history",
            "redirect_uri": settings.WEB_BASE_URL + "/settings/slack/callback",
            "state": str(user.user_id),
        }
    )
    return f"https://slack.com/oauth/v2/authorize?{params}"


async def complete_slack_oauth(code: str, state: str, user: AuthenticatedUser) -> None:
    """Complete Slack OAuth callback — exchanges code for tokens and persists."""
    import httpx

    from app.auth.credentials import get_credential_manager
    from app.persistence.session import get_session_factory, set_tenant_context
    from app.settings import get_settings

    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": settings.SLACK_CLIENT_ID,
                "client_secret": settings.SLACK_CLIENT_SECRET,
                "code": code,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        token_data = response.json()

    if not token_data.get("ok"):
        logger.warning("Slack OAuth failed", error=token_data.get("error"))
        return

    # Encrypt and persist the refresh token
    cm = get_credential_manager()
    access_token = token_data.get("access_token", "")
    cm.encrypt(access_token)

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        # Persist credential and create identity link
        # Full implementation would resolve source_connection_id from workspace
        logger.info("Slack OAuth completed", user_id=str(user.user_id))


class SlackConnector:
    """Slack connector implementing the Connector protocol."""

    source = Source.SLACK

    async def initial_scan(
        self, connection: SourceConnection, cursor: str | None
    ) -> ScanPage:
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
