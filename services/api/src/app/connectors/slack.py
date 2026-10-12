"""Slack connector — OAuth flows and connector implementation."""

from __future__ import annotations

from http import HTTPStatus

from app.auth.oidc import AuthenticatedUser
from app.connectors.errors import ConnectorError, ForbiddenError, PermanentError, RateLimitedError
from app.connectors.http import json_body, request
from app.connectors.oauth import pack_tokens
from app.connectors.parsing import strip_html
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

    The oauth_state is a short-lived signed binding passed through to the
    provider and back to the callback.
    """
    from urllib.parse import urlencode

    from app.settings import get_settings

    settings = get_settings()
    if not oauth_state:
        raise ValueError("AUTH_INVALID_TOKEN")
    state_val = oauth_state
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
            "scope": "",
            "user_scope": " ".join(bot_scopes),
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

    authed_user = token_data.get("authed_user", {})
    access_token = authed_user.get("access_token", "")
    if not access_token:
        logger.warning("Slack OAuth: delegated user token was not returned")
        return

    # Extract workspace and user identity
    team = token_data.get("team", {})
    workspace_id = team.get("id", "slack-workspace")
    workspace_name = team.get("name", "Slack")
    provider_subject = authed_user.get("id", "slack-user")

    # Encrypt and persist the access token
    cm = get_credential_manager()
    payload = cm.encrypt(pack_tokens(access_token))

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

    async def initial_scan(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ScanPage:
        credential = _require_credential(credential)
        response = await request(
            "GET",
            "https://slack.com/api/conversations.list",
            access_token=credential.access_token,
            params={
                "types": "public_channel,private_channel,mpim,im",
                "limit": 200,
                "cursor": cursor or "",
                "exclude_archived": "true",
            },
        )
        payload = _slack_body(response)
        objects: list[dict[str, object]] = []
        channels = payload.get("channels")
        for channel in channels if isinstance(channels, list) else []:
            if not isinstance(channel, dict) or not channel.get("id"):
                continue
            history = await _slack_request(
                "conversations.history",
                credential,
                {"channel": channel["id"], "limit": 100},
            )
            messages = history.get("messages")
            for message in messages if isinstance(messages, list) else []:
                if not isinstance(message, dict) or not message.get("ts"):
                    continue
                ts = str(message["ts"])
                objects.append(
                    {
                        "external_object_id": f"{channel['id']}-{ts}",
                        "object_type": "message",
                        "parent_external_id": str(channel["id"]),
                        "title": str(channel.get("name") or channel["id"]),
                        "source_revision": ts,
                    }
                )
        metadata = payload.get("response_metadata")
        next_value = metadata.get("next_cursor") if isinstance(metadata, dict) else None
        return ScanPage(objects=objects, next_cursor=str(next_value) if next_value else None)

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
        channel_id, _, thread_ts = external_object_id.partition("-")
        if not channel_id or not thread_ts:
            return FetchObjectResult(
                outcome=ConnectorOutcome.PERMANENT_ERROR,
                external_object_id=external_object_id,
            )
        payload = await _slack_request(
            "conversations.replies",
            credential,
            {"channel": channel_id, "ts": thread_ts, "limit": 100},
        )
        messages = payload.get("messages", [])
        if not isinstance(messages, list) or not messages:
            return FetchObjectResult(
                outcome=ConnectorOutcome.NOT_FOUND,
                external_object_id=external_object_id,
            )
        text_parts = []
        latest = thread_ts
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("text"):
                text_parts.append(str(message["text"]))
            if message.get("ts") and str(message["ts"]) > latest:
                latest = str(message["ts"])
        return FetchObjectResult(
            outcome=ConnectorOutcome.SUCCESS,
            external_object_id=external_object_id,
            source_revision=latest,
            object_type="message",
            parent_external_id=channel_id,
            canonical_url=await _permalink(credential, channel_id, thread_ts),
            title=f"Slack thread {thread_ts}",
            content=strip_html("\n\n".join(text_parts)),
            provider_metadata={
                "workspace_id": connection.external_workspace_id,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
            },
        )

    async def fetch_acl(
        self,
        connection: SourceConnection,
        external_object_id: str,
        credential: DelegatedCredential | None = None,
    ) -> FetchAclResult:
        _require_credential(credential)
        # Slack visibility is channel membership based and is authoritatively
        # checked per requesting identity at query time.
        return FetchAclResult(outcome=ConnectorOutcome.SUCCESS)

    async def check_access(  # noqa: PLR0911
        self, user_credential: DelegatedCredential, external_object_id: str
    ) -> AccessCheckResult:
        """Check whether the delegated Slack token can read the message's channel."""
        import httpx

        channel_id = external_object_id.split("-", maxsplit=1)[0]
        if not channel_id:
            return AccessCheckResult(outcome=AccessOutcome.DENY, reason_code="invalid_object_id")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://slack.com/api/conversations.info",
                    headers={"Authorization": f"Bearer {user_credential.access_token}"},
                    params={"channel": channel_id},
                )
        except httpx.TimeoutException:
            return AccessCheckResult(outcome=AccessOutcome.TIMEOUT, reason_code="provider_timeout")
        except httpx.HTTPError:
            return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="provider_error")

        request_id = response.headers.get("x-slack-req-id")
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            return AccessCheckResult(
                outcome=AccessOutcome.EXPIRED_CREDENTIAL,
                reason_code="credential_expired",
                provider_request_id=request_id,
            )
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            return AccessCheckResult(
                outcome=AccessOutcome.TIMEOUT,
                reason_code="provider_rate_limited",
                provider_request_id=request_id,
            )

        data = response.json()
        if data.get("ok") is True:
            return AccessCheckResult(
                outcome=AccessOutcome.ALLOW,
                reason_code="provider_allow",
                provider_request_id=request_id,
            )

        error = str(data.get("error") or "provider_deny")
        if error in {"channel_not_found", "not_in_channel", "is_archived"}:
            return AccessCheckResult(
                outcome=AccessOutcome.DENY,
                reason_code=error,
                provider_request_id=request_id,
            )
        if error in {"invalid_auth", "account_inactive", "token_revoked"}:
            return AccessCheckResult(
                outcome=AccessOutcome.EXPIRED_CREDENTIAL,
                reason_code=error,
                provider_request_id=request_id,
            )
        return AccessCheckResult(
            outcome=AccessOutcome.UNKNOWN,
            reason_code=error,
            provider_request_id=request_id,
        )


def _require_credential(credential: DelegatedCredential | None) -> DelegatedCredential:
    if credential is None or not credential.access_token:
        raise ValueError("SOURCE_CREDENTIAL_EXPIRED")
    return credential


async def _slack_request(
    method: str,
    credential: DelegatedCredential,
    params: dict[str, object],
) -> dict[str, object]:
    response = await request(
        "GET",
        f"https://slack.com/api/{method}",
        access_token=credential.access_token,
        params=params,
    )
    return _slack_body(response)


async def _permalink(
    credential: DelegatedCredential,
    channel_id: str,
    message_ts: str,
) -> str | None:
    try:
        payload = await _slack_request(
            "chat.getPermalink",
            credential,
            {"channel": channel_id, "message_ts": message_ts},
        )
    except ConnectorError:
        return None
    permalink = payload.get("permalink")
    return str(permalink) if permalink else None


def _slack_body(response: object) -> dict[str, object]:
    payload = json_body(response)  # type: ignore[arg-type]
    if payload.get("ok") is True:
        return payload
    error = str(payload.get("error") or "Slack API error")
    if error in {"not_authed", "invalid_auth", "token_revoked", "account_inactive"}:
        raise ForbiddenError("Slack credential rejected")
    if error == "ratelimited":
        raise RateLimitedError()
    raise PermanentError("Slack operation failed")
