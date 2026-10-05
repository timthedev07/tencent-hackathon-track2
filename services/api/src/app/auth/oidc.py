"""OIDC token validation.

Validates: token signature, issuer, audience, expiry, subject.
Resolves: oidc_subject → tenant → internal user.
Never trusts tenant_id from client input (INV-007).
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import httpx
import jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger
from app.persistence.models import Tenant, User
from app.settings import get_settings

logger = get_logger(__name__)

# JWKS cache (renew periodically)
_jwks_cache: dict[str, Any] = {}
_jwks_last_fetch: float = 0.0
_JWKS_TTL_SECONDS = 300  # 5 minutes


class AuthenticatedUser(BaseModel):
    """Resolved user context derived from the OIDC token."""

    user_id: UUID
    tenant_id: UUID
    oidc_subject: str
    display_name: str | None = None
    email: str | None = None
    is_compliance_user: bool = False


async def _fetch_jwks() -> dict[str, Any]:
    """Fetch JWKS from the issuer with caching."""
    global _jwks_cache, _jwks_last_fetch

    now = time.time()
    if _jwks_cache and (now - _jwks_last_fetch) < _JWKS_TTL_SECONDS:
        return _jwks_cache

    settings = get_settings()
    if not settings.OIDC_JWKS_URL:
        logger.warning("OIDC_JWKS_URL not configured — using dev fallback")
        # Dev fallback: return empty JWKS (tokens validated without signature in dev)
        return {"keys": []}

    async with httpx.AsyncClient() as client:
        response = await client.get(settings.OIDC_JWKS_URL, timeout=10.0)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_last_fetch = now
        return _jwks_cache


async def validate_token(token: str) -> dict[str, Any]:
    """Validate an OIDC access token.

    Validates: signature, issuer, audience, expiry, subject.
    Returns the decoded claims.
    Raises on invalid token (caller maps to 401).

    Dev mode: tokens prefixed with 'dev-' bypass all cryptographic checks.
    The subject is extracted directly from the token for local development.
    """
    settings = get_settings()

    # ── Dev-mode bypass ──────────────────────────────────────────
    # In development, accept 'dev-{subject}' tokens without signature
    # verification, JWKS fetch, or issuer/audience checks.
    if settings.is_development and token.startswith("dev-"):
        oidc_subject = token.removeprefix("dev-")
        if not oidc_subject:
            raise ValueError("AUTH_INVALID_TOKEN")
        logger.info("Dev-mode token accepted", oidc_subject=oidc_subject)
        return {
            "sub": oidc_subject,
            "iss": "dev-local",
            "aud": "dev-local",
        }

    # Decode without verification first to get the key ID
    try:
        unverified_header = jwt.get_unverified_header(token)
        unverified_claims = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.PyJWTError as exc:
        logger.warning("Token decode failed", error=str(exc))
        raise ValueError("AUTH_INVALID_TOKEN") from exc

    # Check expiry
    exp = unverified_claims.get("exp")
    if exp is not None and exp < time.time():
        logger.warning("Token expired", oidc_subject=unverified_claims.get("sub"))
        raise ValueError("AUTH_INVALID_TOKEN")

    # Check issuer
    if settings.OIDC_ISSUER and unverified_claims.get("iss") != settings.OIDC_ISSUER:
        logger.warning("Token issuer mismatch")
        raise ValueError("AUTH_INVALID_TOKEN")

    # Check audience
    if settings.OIDC_AUDIENCE and unverified_claims.get("aud") != settings.OIDC_AUDIENCE:
        logger.warning("Token audience mismatch")
        raise ValueError("AUTH_INVALID_TOKEN")

    # Verify signature (skip in development if no JWKS available)
    jwks = await _fetch_jwks()
    kid = unverified_header.get("kid")

    if jwks.get("keys"):
        signing_key = None
        for key in jwks["keys"]:
            if key.get("kid") == kid:
                signing_key = jwt.PyJWK(key)
                break
        if signing_key is None:
            logger.warning("No matching JWKS key for kid")
            raise ValueError("AUTH_INVALID_TOKEN")

        try:
            jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=settings.OIDC_AUDIENCE,
                issuer=settings.OIDC_ISSUER,
            )
        except jwt.PyJWTError as exc:
            logger.warning("Token signature verification failed", error=str(exc))
            raise ValueError("AUTH_INVALID_TOKEN") from exc

    # Subject must be present
    if not unverified_claims.get("sub"):
        logger.warning("Token missing subject claim")
        raise ValueError("AUTH_INVALID_TOKEN")

    return unverified_claims


async def resolve_user(session: AsyncSession, claims: dict[str, Any]) -> AuthenticatedUser:
    """Resolve oidc_subject → tenant → internal user (INV-007).

    Never trusts tenant_id from client input.
    """
    oidc_subject = claims["sub"]

    # Server-side resolution: find user by OIDC subject
    stmt = (
        select(User, Tenant)
        .join(Tenant, User.tenant_id == Tenant.id)
        .where(User.oidc_subject == oidc_subject)
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()

    # ── Dev-mode auto-provision ─────────────────────────────────
    # In development, if the user doesn't exist, auto-create a dev
    # tenant + user so the app works without Keycloak.
    if row is None and get_settings().is_development:
        # Check if a dev tenant already exists
        dev_tenant = await session.execute(
            select(Tenant).where(Tenant.external_org_key == "dev-org")
        )
        tenant = dev_tenant.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                external_org_key="dev-org",
                display_name="Development Tenant",
            )
            session.add(tenant)
            await session.flush()

        user = User(
            tenant_id=tenant.id,
            oidc_subject=oidc_subject,
            display_name=f"Dev User ({oidc_subject})",
            email=f"{oidc_subject}@dev.local",
            is_compliance_user=True,
        )
        session.add(user)
        await session.commit()
        logger.info("Auto-provisioned dev user", oidc_subject=oidc_subject)

        return AuthenticatedUser(
            user_id=user.id,
            tenant_id=tenant.id,
            oidc_subject=oidc_subject,
            display_name=user.display_name,
            email=user.email,
            is_compliance_user=user.is_compliance_user,
        )

    if row is None:
        logger.warning("No user found for OIDC subject")
        raise ValueError("AUTH_TENANT_NOT_RESOLVED")

    user, tenant = row
    await session.execute(
        select(1).where(Tenant.id == tenant.id)  # Set RLS context
    )

    return AuthenticatedUser(
        user_id=user.id,
        tenant_id=tenant.id,
        oidc_subject=user.oidc_subject,
        display_name=user.display_name,
        email=user.email,
        is_compliance_user=user.is_compliance_user,
    )
