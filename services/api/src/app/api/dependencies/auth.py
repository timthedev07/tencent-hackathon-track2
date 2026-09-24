"""FastAPI dependencies for authentication and authorisation."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import AuthenticatedUser, resolve_user, validate_token
from app.observability.logging import get_logger
from app.persistence.session import get_session, set_tenant_context

logger = get_logger(__name__)


async def get_bearer_token(
    authorization: str | None = Header(default=None),
) -> str:
    """Extract the Bearer token from the Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Authentication required."}},
        )
    return authorization.removeprefix("Bearer ").strip()


async def get_authenticated_user(
    token: str = Depends(get_bearer_token),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    """Validate the OIDC token and resolve the internal user.

    INV-007: tenant identity is server-derived, never from client input.
    """
    try:
        claims = await validate_token(token)
    except ValueError as exc:
        error_code = str(exc) if str(exc) else "AUTH_INVALID_TOKEN"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": error_code,
                    "message": "Your session is invalid. Please sign in again.",
                }
            },
        ) from exc

    try:
        user = await resolve_user(session, claims)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "AUTH_TENANT_NOT_RESOLVED",
                    "message": "Your account is not provisioned for this service.",
                }
            },
        ) from exc

    # Set RLS context for this transaction
    await set_tenant_context(session, user.tenant_id)

    return user


async def get_compliance_user(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    """Require compliance role for audit endpoints."""

    if not user.is_compliance_user:
        await _check_compliance_db(session, user.user_id)

    return user


async def _check_compliance_db(session: AsyncSession, user_id: UUID) -> None:
    from app.auth.identities import is_compliance_user

    if not await is_compliance_user(session, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "AUTH_TENANT_NOT_RESOLVED",
                    "message": "Compliance role required.",
                }
            },
        )
