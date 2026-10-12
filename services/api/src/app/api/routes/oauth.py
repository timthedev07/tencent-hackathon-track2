"""OAuth callback routes for source identity linking.

GET  /v1/oauth/atlassian/start   — returns authorisation URL (requires Bearer token)
GET  /v1/oauth/atlassian/callback — handles provider redirect and persists tokens
GET  /v1/oauth/slack/start
GET  /v1/oauth/slack/callback
GET  /v1/oauth/google/start
GET  /v1/oauth/google/callback
GET  /v1/oauth/connections — list the current user's connected sources

The OAuth flow works as follows:
1. Frontend calls /start with a Bearer token → gets an authoriseUrl
2. Browser navigates to authoriseUrl → user grants access on provider site
3. Provider redirects browser to /callback?code=...&state=...
   - state is a short-lived signed binding for the tenant and user
4. API exchanges code for tokens, persists to DB
5. API redirects browser back to frontend /settings?status=connected&source=...
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_authenticated_user
from app.auth.oauth_state import create_oauth_state, verify_oauth_state
from app.auth.oidc import AuthenticatedUser
from app.domain.source_connection_service import (
    SourceConnectionNotFoundError,
    SourceConnectionNotSyncableError,
    SourceConnectionSummary,
    SourceSyncUnavailableError,
    list_user_connections,
    queue_connection_sync,
)
from app.domain.source_mode import active_connection_mode
from app.domain.types import Source
from app.observability.logging import get_logger
from app.persistence.session import get_session
from app.settings import get_settings

router = APIRouter()
logger = get_logger(__name__)


class OAuthStartResponse(BaseModel):
    authoriseUrl: str


class ConnectionStatus(BaseModel):
    source: str
    connected: bool
    display_name: str | None = None
    connectionId: str | None = None
    mode: str | None = None
    canSync: bool = False


class ConnectionsResponse(BaseModel):
    dataMode: str
    connections: list[ConnectionStatus]


class SyncConnectionResponse(BaseModel):
    connectionId: str
    status: str


# ── Connection status ────────────────────────────────────────────────


@router.get("/connections", response_model=ConnectionsResponse)
async def list_connections(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> ConnectionsResponse:
    """List the current user's connection status for all sources."""
    sources = [Source.CONFLUENCE, Source.JIRA, Source.SLACK, Source.GOOGLE_DRIVE]
    summaries = await list_user_connections(session, user)
    by_source: dict[str, list[SourceConnectionSummary]] = {}
    for summary in summaries:
        by_source.setdefault(summary.source, []).append(summary)
    result_list: list[ConnectionStatus] = []

    for source in sources:
        matches = by_source.get(source.value, [])
        summary = matches[0] if len(matches) == 1 else None

        result_list.append(
            ConnectionStatus(
                source=source.value,
                connected=summary is not None,
                display_name=summary.display_name if summary else None,
                connectionId=str(summary.connection_id) if summary else None,
                mode=summary.mode if summary else None,
                canSync=summary is not None and summary.mode == "delegated_oauth",
            )
        )

    return ConnectionsResponse(
        dataMode=get_settings().SOURCE_DATA_MODE,
        connections=result_list,
    )


@router.post(
    "/connections/{connection_id}/sync",
    response_model=SyncConnectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_connection(
    connection_id: UUID,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> SyncConnectionResponse:
    """Queue ingestion for one exact delegated-OAuth connection."""
    try:
        result = await queue_connection_sync(session, user, connection_id)
    except SourceConnectionNotFoundError as exc:
        raise _connection_error(
            status.HTTP_404_NOT_FOUND,
            "SOURCE_CONNECTION_NOT_FOUND",
            "Source connection not found.",
        ) from exc
    except SourceConnectionNotSyncableError as exc:
        raise _connection_error(
            status.HTTP_409_CONFLICT,
            "SOURCE_CONNECTION_NOT_SYNCABLE",
            "Demo sources cannot be synchronised.",
        ) from exc
    except SourceSyncUnavailableError as exc:
        raise _connection_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SOURCE_SYNC_FAILED",
            "The source sync could not be queued. Please try again.",
        ) from exc
    return SyncConnectionResponse(**result)


# ── Atlassian ────────────────────────────────────────────────────────


@router.get("/atlassian/start", response_model=OAuthStartResponse)
async def start_atlassian_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Atlassian OAuth flow for source identity linking."""
    _require_live_mode()
    from app.connectors.confluence import start_atlassian_oauth as start

    oauth_state = create_oauth_state(user.user_id, user.tenant_id)
    url = await start(user, oauth_state=oauth_state)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/atlassian/callback")
async def atlassian_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Complete Atlassian OAuth callback — redirects to frontend settings."""
    from app.connectors.confluence import complete_atlassian_oauth

    user = await _resolve_user_from_state(state)
    try:
        await complete_atlassian_oauth(code, state, user)
    except Exception as exc:
        logger.warning("Atlassian OAuth callback failed", error=str(exc))
        return _redirect_to_settings("error", "atlassian", "OAuth connection failed")

    return _redirect_to_settings("connected", "atlassian")


# ── Slack ────────────────────────────────────────────────────────────


@router.get("/slack/start", response_model=OAuthStartResponse)
async def start_slack_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Slack OAuth flow."""
    _require_live_mode()
    from app.connectors.slack import start_slack_oauth as start

    oauth_state = create_oauth_state(user.user_id, user.tenant_id)
    url = await start(user, oauth_state=oauth_state)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/slack/callback")
async def slack_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Complete Slack OAuth callback — redirects to frontend settings."""
    from app.connectors.slack import complete_slack_oauth

    user = await _resolve_user_from_state(state)
    try:
        await complete_slack_oauth(code, state, user)
    except Exception as exc:
        logger.warning("Slack OAuth callback failed", error=str(exc))
        return _redirect_to_settings("error", "slack", "OAuth connection failed")

    return _redirect_to_settings("connected", "slack")


# ── Google Drive ─────────────────────────────────────────────────────


@router.get("/google/start", response_model=OAuthStartResponse)
async def start_google_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Google OAuth flow."""
    _require_live_mode()
    from app.connectors.google_drive import start_google_oauth as start

    oauth_state = create_oauth_state(user.user_id, user.tenant_id)
    url = await start(user, oauth_state=oauth_state)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Complete Google OAuth callback — redirects to frontend settings."""
    from app.connectors.google_drive import complete_google_oauth

    user = await _resolve_user_from_state(state)
    try:
        await complete_google_oauth(code, state, user)
    except Exception as exc:
        logger.warning("Google OAuth callback failed", error=str(exc))
        return _redirect_to_settings("error", "google", "OAuth connection failed")

    return _redirect_to_settings("connected", "google")


# ── Helpers ──────────────────────────────────────────────────────────


def _connection_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


def _require_live_mode() -> None:
    if active_connection_mode() != "delegated_oauth":
        raise _connection_error(
            status.HTTP_409_CONFLICT,
            "LIVE_MODE_REQUIRED",
            "Switch SOURCE_DATA_MODE to live before connecting a provider.",
        )


async def _resolve_user_from_state(state: str) -> AuthenticatedUser:
    """Resolve the user from the OAuth state parameter.

    The state is a short-lived signed binding containing only internal IDs.
    It is never a bearer token and cannot be used as an API credential.
    """
    try:
        tenant_id, user_id = verify_oauth_state(state)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid OAuth state"}},
        ) from exc

    from app.persistence.models import Tenant, User
    from app.persistence.session import get_session_factory, set_tenant_context

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (
            await session.execute(
                select(User, Tenant)
                .join(Tenant, User.tenant_id == Tenant.id)
                .where(User.id == user_id, User.tenant_id == tenant_id)
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid OAuth state"}},
            )
        user, _tenant = row
        return AuthenticatedUser(
            user_id=user.id,
            tenant_id=user.tenant_id,
            oidc_subject=user.oidc_subject,
            display_name=user.display_name,
            email=user.email,
            is_compliance_user=user.is_compliance_user,
        )


def _redirect_to_settings(
    status_val: str, source: str, error: str | None = None
) -> RedirectResponse:
    """Redirect the browser back to the frontend settings page."""
    settings = get_settings()
    base = settings.WEB_BASE_URL.rstrip("/")
    params = f"?status={status_val}&source={source}"
    if error:
        import urllib.parse

        params += f"&error={urllib.parse.quote(error)}"
    return RedirectResponse(url=f"{base}/settings{params}")
