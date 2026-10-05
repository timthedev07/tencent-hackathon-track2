"""Dev-only authentication endpoint.

POST /v1/auth/dev-login
  Body: { "subject": "alice" }
  Returns: { "token": "dev-alice", "subject": "alice" }

This endpoint is ONLY available when APP_ENV=development.
In production, it returns 404.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.settings import get_settings

router = APIRouter()


class DevLoginRequest(BaseModel):
    subject: str = "alice"


class DevLoginResponse(BaseModel):
    token: str
    subject: str


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
