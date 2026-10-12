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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_authenticated_user
from app.auth.identities import find_identity_link
from app.auth.oauth_state import create_oauth_state, verify_oauth_state
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
    sources = [Source.CONFLUENCE, Source.JIRA, Source.SLACK, Source.GOOGLE_DRIVE]
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
    except ValueError as exc:
        error_messages = {
            "ATLASSIAN_IDENTITY_UNAVAILABLE": (
                "Atlassian identity could not be verified. Enable the User Identity API "
                "and read:me, then reconnect."
            ),
            "IDENTITY_LINK_ALREADY_CLAIMED": (
                "This Atlassian account is already linked to another Relay user."
            ),
            "IDENTITY_LINK_SUBJECT_MISMATCH": (
                "This Relay user already has a different Atlassian identity linked."
            ),
        }
        error_code = str(exc)
        logger.warning("Atlassian OAuth callback failed", error=error_code)
        return _redirect_to_settings(
            "error", "atlassian", error_messages.get(error_code, "OAuth connection failed")
        )
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
    except ValueError as exc:
        error_messages = {
            "GOOGLE_IDENTITY_UNAVAILABLE": (
                "Google identity could not be verified. Please grant the requested "
                "identity permissions and reconnect."
            ),
            "IDENTITY_LINK_ALREADY_CLAIMED": (
                "This Google account is already linked to another Relay user."
            ),
            "IDENTITY_LINK_SUBJECT_MISMATCH": (
                "This Relay user already has a different Google identity linked."
            ),
        }
        error_code = str(exc)
        logger.warning("Google OAuth callback failed", error=error_code)
        return _redirect_to_settings(
            "error", "google", error_messages.get(error_code, "OAuth connection failed")
        )
    except Exception as exc:
        logger.warning("Google OAuth callback failed", error=str(exc))
        return _redirect_to_settings("error", "google", "OAuth connection failed")

    return _redirect_to_settings("connected", "google")


# ── Helpers ──────────────────────────────────────────────────────────


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
                select(User, Tenant).join(Tenant, User.tenant_id == Tenant.id).where(
                    User.id == user_id, User.tenant_id == tenant_id
                )
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
