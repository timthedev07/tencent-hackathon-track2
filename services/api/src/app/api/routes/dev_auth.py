"""Authentication session endpoints.

POST /v1/auth/dev-login
  Body: { "subject": "alice" }
  Returns: { "token": "dev-alice", "subject": "alice" }

This endpoint is ONLY available when APP_ENV=development.
In production, it returns 404.

GET /v1/auth/session
  Returns the authenticated user's display details and compliance role.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.dependencies.auth import get_authenticated_user
from app.auth.oidc import AuthenticatedUser
from app.settings import get_settings

router = APIRouter()


class DevLoginRequest(BaseModel):
    subject: str = "alice"


class DevLoginResponse(BaseModel):
    token: str
    subject: str


class SessionResponse(BaseModel):
    """Safe user details required to render the application shell."""

    displayName: str | None = None
    isComplianceUser: bool


@router.post("/dev-login", response_model=DevLoginResponse)
async def dev_login(body: DevLoginRequest) -> DevLoginResponse:
    """Issue a dev-mode token. Only available in development."""
    settings = get_settings()
    if not settings.is_development:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    subject = body.subject.strip() or "alice"
    return DevLoginResponse(token=f"dev-{subject}", subject=subject)


@router.get("/session", response_model=SessionResponse)
async def get_session(user: AuthenticatedUser = Depends(get_authenticated_user)) -> SessionResponse:
    """Return the current user's UI-safe session metadata."""
    return SessionResponse(
        displayName=user.display_name,
        isComplianceUser=user.is_compliance_user,
    )
