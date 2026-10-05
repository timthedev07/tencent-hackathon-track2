"""OAuth callback routes for source identity linking.

GET  /v1/oauth/atlassian/start   — returns authorisation URL (requires Bearer token)
GET  /v1/oauth/atlassian/callback — handles provider redirect, persists tokens, redirects to frontend
GET  /v1/oauth/slack/start
GET  /v1/oauth/slack/callback
GET  /v1/oauth/google/start
GET  /v1/oauth/google/callback
GET  /v1/oauth/connections — list the current user's connected sources

The OAuth flow works as follows:
1. Frontend calls /start with a Bearer token → gets an authoriseUrl
2. Browser navigates to authoriseUrl → user grants access on provider site
3. Provider redirects browser to /callback?code=...&state=...
   - state encodes the user's dev token so the callback can authenticate
4. API exchanges code for tokens, persists to DB
5. API redirects browser back to frontend /settings?status=connected&source=...
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_authenticated_user
from app.auth.identities import find_identity_link
from app.auth.oidc import AuthenticatedUser
from app.domain.types import Source
from app.observability.logging import get_logger
from app.persistence.models import SourceConnection
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


class ConnectionsResponse(BaseModel):
    connections: list[ConnectionStatus]


# ── Connection status ────────────────────────────────────────────────


@router.get("/connections", response_model=ConnectionsResponse)
async def list_connections(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> ConnectionsResponse:
    """List the current user's connection status for all sources."""
    sources = [Source.CONFLUENCE, Source.SLACK, Source.GOOGLE_DRIVE]
    result_list: list[ConnectionStatus] = []

    for source in sources:
        # Check if user has a verified identity link for this source
        link = await find_identity_link(session, user.user_id, source)
        display_name = None
        if link is not None:
            stmt = select(SourceConnection).where(
                SourceConnection.id == link.source_connection_id
            )
            conn_result = await session.execute(stmt)
            conn = conn_result.scalar_one_or_none()
            if conn is not None:
                display_name = conn.display_name

        result_list.append(
            ConnectionStatus(
                source=source.value,
                connected=link is not None,
                display_name=display_name,
            )
        )

    return ConnectionsResponse(connections=result_list)


# ── Atlassian ────────────────────────────────────────────────────────


@router.get("/atlassian/start", response_model=OAuthStartResponse)
async def start_atlassian_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Atlassian OAuth flow for source identity linking."""
    from app.connectors.confluence import start_atlassian_oauth as start

    oauth_state = _extract_token(authorization)
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
        return _redirect_to_settings("error", "atlassian", str(exc))

    return _redirect_to_settings("connected", "atlassian")


# ── Slack ────────────────────────────────────────────────────────────


@router.get("/slack/start", response_model=OAuthStartResponse)
async def start_slack_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Slack OAuth flow."""
    from app.connectors.slack import start_slack_oauth as start

    oauth_state = _extract_token(authorization)
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
        return _redirect_to_settings("error", "slack", str(exc))

    return _redirect_to_settings("connected", "slack")


# ── Google Drive ─────────────────────────────────────────────────────


@router.get("/google/start", response_model=OAuthStartResponse)
async def start_google_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Google OAuth flow."""
    from app.connectors.google_drive import start_google_oauth as start

    oauth_state = _extract_token(authorization)
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
        return _redirect_to_settings("error", "google", str(exc))

    return _redirect_to_settings("connected", "google")


# ── Helpers ──────────────────────────────────────────────────────────


async def _resolve_user_from_state(state: str) -> AuthenticatedUser:
    """Resolve the user from the OAuth state parameter.

    In dev mode, state is the dev token (e.g. 'dev-alice').
    In production, state would be a signed session reference.
    """
    settings = get_settings()

    if settings.is_development and state.startswith("dev-"):
        from app.auth.oidc import resolve_user, validate_token
        from app.persistence.session import get_session_factory

        claims = await validate_token(state)
        factory = get_session_factory()
        async with factory() as session:
            user = await resolve_user(session, claims)
            return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid OAuth state"}},
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


def _extract_token(authorization: str | None) -> str:
    """Extract the raw Bearer token from the Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        return ""
    return authorization.removeprefix("Bearer ").strip()
