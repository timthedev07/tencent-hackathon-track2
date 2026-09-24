"""OAuth callback routes for source identity linking.

POST /v1/oauth/atlassian/start
GET  /v1/oauth/atlassian/callback
POST /v1/oauth/slack/start
GET  /v1/oauth/slack/callback
POST /v1/oauth/google/start
GET  /v1/oauth/google/callback
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies.auth import get_authenticated_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


class OAuthStartResponse(BaseModel):
    authoriseUrl: str


class OAuthCallbackResponse(BaseModel):
    status: str = "linked"
    source: str


@router.get("/atlassian/start", response_model=OAuthStartResponse)
async def start_atlassian_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> OAuthStartResponse:
    """Initiate Atlassian OAuth flow for source identity linking."""
    from app.connectors.confluence import start_atlassian_oauth as start

    url = await start(user)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/atlassian/callback", response_model=OAuthCallbackResponse)
async def atlassian_callback(
    code: str,
    state: str,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> OAuthCallbackResponse:
    """Complete Atlassian OAuth callback — binds immutable provider subject."""
    from app.connectors.confluence import complete_atlassian_oauth

    await complete_atlassian_oauth(code, state, user)
    return OAuthCallbackResponse(source="atlassian")


@router.get("/slack/start", response_model=OAuthStartResponse)
async def start_slack_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> OAuthStartResponse:
    """Initiate Slack OAuth flow."""
    from app.connectors.slack import start_slack_oauth as start

    url = await start(user)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/slack/callback", response_model=OAuthCallbackResponse)
async def slack_callback(
    code: str,
    state: str,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> OAuthCallbackResponse:
    """Complete Slack OAuth callback."""
    from app.connectors.slack import complete_slack_oauth

    await complete_slack_oauth(code, state, user)
    return OAuthCallbackResponse(source="slack")


@router.get("/google/start", response_model=OAuthStartResponse)
async def start_google_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> OAuthStartResponse:
    """Initiate Google OAuth flow."""
    from app.connectors.google_drive import start_google_oauth as start

    url = await start(user)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/google/callback", response_model=OAuthCallbackResponse)
async def google_callback(
    code: str,
    state: str,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> OAuthCallbackResponse:
    """Complete Google OAuth callback."""
    from app.connectors.google_drive import complete_google_oauth

    await complete_google_oauth(code, state, user)
    return OAuthCallbackResponse(source="google")
