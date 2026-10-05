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


async def start_slack_oauth(user: AuthenticatedUser, oauth_state: str = "") -> str:
    """Initiate Slack OAuth flow.

    The oauth_state is passed through to the provider and back to the callback.
    """
    from urllib.parse import urlencode

    from app.settings import get_settings

    settings = get_settings()
    state_val = oauth_state or str(user.user_id)
    redirect_uri = settings.SLACK_REDIRECT_URI or (
        settings.APP_BASE_URL + "/v1/oauth/slack/callback"
    )
    # Bot token scopes — must match the scopes configured on the Slack app
    # (api.slack.com → OAuth & Permissions → Bot Token Scopes).
    # Requesting a scope here that is NOT configured on the app causes Slack
    # to reject the install with "the app is requesting scopes it does not have".
    bot_scopes = [
        "channels:history",
        "channels:read",
        "groups:history",
        "groups:read",
        "mpim:history",
        "mpim:read",
        "im:history",
        "im:read",
        "team:read",
        "users:read",
        "users.profile:read",
    ]
    params = urlencode(
        {
            "client_id": settings.SLACK_CLIENT_ID,
            "scope": " ".join(bot_scopes),
            "redirect_uri": redirect_uri,
            "state": state_val,
        }
    )
    return f"https://slack.com/oauth/v2/authorize?{params}"


async def complete_slack_oauth(code: str, state: str, user: AuthenticatedUser) -> None:
    """Complete Slack OAuth callback — exchanges code for tokens and persists."""
    import httpx
    from sqlalchemy import select as sa_select

    from app.auth.credentials import get_credential_manager
    from app.auth.identities import create_identity_link
    from app.domain.types import CredentialType
    from app.persistence.models import EncryptedCredential, SourceConnection
    from app.persistence.session import get_session_factory, set_tenant_context
    from app.settings import get_settings

    settings = get_settings()
    redirect_uri = settings.SLACK_REDIRECT_URI or (
        settings.APP_BASE_URL + "/v1/oauth/slack/callback"
    )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": settings.SLACK_CLIENT_ID,
                "client_secret": settings.SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        token_data = response.json()

    if not token_data.get("ok"):
        logger.warning("Slack OAuth failed", error=token_data.get("error"))
        return

    access_token = token_data.get("access_token", "")
    if not access_token:
        logger.warning("Slack OAuth: no access token returned")
        return

    # Extract workspace and user identity
    team = token_data.get("team", {})
    workspace_id = team.get("id", "slack-workspace")
    workspace_name = team.get("name", "Slack")
    authed_user = token_data.get("authed_user", {})
    provider_subject = authed_user.get("id", "slack-user")

    # Encrypt and persist the access token
    cm = get_credential_manager()
    payload = cm.encrypt(access_token)

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)

        # Find or create the source connection
        stmt = sa_select(SourceConnection).where(
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.source == "slack",
            SourceConnection.external_workspace_id == workspace_id,
        )
        result = await session.execute(stmt)
        connection = result.scalar_one_or_none()

        if connection is None:
            connection = SourceConnection(
                tenant_id=user.tenant_id,
                source="slack",
                external_workspace_id=workspace_id,
                display_name=workspace_name,
                status="connected",
            )
            session.add(connection)
            await session.flush()

        # Persist the encrypted credential
        credential = EncryptedCredential(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            source_connection_id=connection.id,
            credential_type=CredentialType.OAUTH_ACCESS.value,
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
            provider_account_type="slack",
        )

        await session.commit()
        logger.info(
            "Slack OAuth completed",
            user_id=str(user.user_id),
            provider_subject=provider_subject,
            workspace=workspace_name,
        )


class SlackConnector:
    """Slack connector implementing the Connector protocol."""

    source = Source.SLACK

    async def initial_scan(self, connection: SourceConnection, cursor: str | None) -> ScanPage:
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
