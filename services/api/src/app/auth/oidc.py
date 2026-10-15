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
from app.persistence.session import set_tenant_context
from app.settings import get_settings

logger = get_logger(__name__)

# JWKS cache (renew periodically)
_jwks_cache: dict[str, Any] = {}
_jwks_last_fetch: float = 0.0
_JWKS_TTL_SECONDS = 300  # 5 minutes
_DEV_COMPLIANCE_SUBJECT = "carol"


def _is_dev_compliance_subject(subject: str) -> bool:
    """Grant the demo compliance role only to the explicit demo subject."""
    return subject.casefold() == _DEV_COMPLIANCE_SUBJECT


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
        if settings.is_production:
            logger.error("OIDC_JWKS_URL is required in production")
            raise ValueError("AUTH_CONFIGURATION_ERROR")
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

    if not unverified_claims.get("sub"):
        raise ValueError("AUTH_INVALID_TOKEN")

    # A signed token is mandatory outside the explicit local development
    # shortcut.  Never accept a decoded-but-unverified token in staging or
    # production when the JWKS endpoint is unavailable.
    jwks = await _fetch_jwks()
    kid = unverified_header.get("kid")

    if not settings.is_development and not jwks.get("keys"):
        logger.error("OIDC JWKS is empty outside development")
        raise ValueError("AUTH_CONFIGURATION_ERROR")

    verified_claims = unverified_claims
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
            verified_claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=settings.OIDC_AUDIENCE,
                issuer=settings.OIDC_ISSUER,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            logger.warning("Token signature verification failed", error=str(exc))
            raise ValueError("AUTH_INVALID_TOKEN") from exc

    return verified_claims


async def resolve_user(session: AsyncSession, claims: dict[str, Any]) -> AuthenticatedUser:
    """Resolve oidc_subject → tenant → internal user (INV-007).

    Never trusts tenant_id from client input.
    """
    oidc_subject = claims["sub"]
    settings = get_settings()

    # Resolve the tenant before touching tenant-scoped tables.  A subject is
    # only unique within an enterprise tenant, so a global subject lookup is
    # both ambiguous and incompatible with FORCE ROW LEVEL SECURITY.
    tenant_claim = claims.get(settings.OIDC_TENANT_CLAIM)
    if isinstance(tenant_claim, list):
        tenant_claim = tenant_claim[0] if len(tenant_claim) == 1 else None
    tenant: Tenant | None = None
    if tenant_claim:
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.external_org_key == str(tenant_claim))
        )
        tenant = tenant_result.scalar_one_or_none()
        if tenant is None:
            raise ValueError("AUTH_TENANT_NOT_RESOLVED")
    elif not settings.is_development:
        raise ValueError("AUTH_TENANT_NOT_RESOLVED")
    else:
        dev_tenant_result = await session.execute(
            select(Tenant).where(Tenant.external_org_key == "dev-org")
        )
        tenant = dev_tenant_result.scalar_one_or_none()

    if tenant is not None:
        await set_tenant_context(session, tenant.id)

    # Server-side resolution: find the subject within the resolved tenant.
    user_filters = [User.oidc_subject == oidc_subject]
    if tenant is not None:
        user_filters.append(User.tenant_id == tenant.id)
    stmt = (
        select(User, Tenant)
        .join(Tenant, User.tenant_id == Tenant.id)
        .where(*user_filters)
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()

    # ── Dev-mode auto-provision ─────────────────────────────────
    # In development, if the user doesn't exist, auto-create a dev
    # tenant + user so the app works without Keycloak.
    if row is None and settings.is_development:
        # Check if a dev tenant already exists
        if tenant is None:
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
        assert tenant is not None
        await set_tenant_context(session, tenant.id)

        user = User(
            tenant_id=tenant.id,
            oidc_subject=oidc_subject,
            display_name=f"Dev User ({oidc_subject})",
            email=f"{oidc_subject}@dev.local",
            is_compliance_user=_is_dev_compliance_subject(oidc_subject),
        )
        session.add(user)
        await session.commit()
        logger.info("Auto-provisioned dev user", oidc_subject=oidc_subject)

        return AuthenticatedUser(
            user_id=UUID(str(user.id)),
            tenant_id=UUID(str(tenant.id)),
            oidc_subject=oidc_subject,
            display_name=user.display_name,
            email=user.email,
            is_compliance_user=user.is_compliance_user,
        )

    if row is None:
        logger.warning("No user found for OIDC subject")
        raise ValueError("AUTH_TENANT_NOT_RESOLVED")

    user, tenant = row
    assert tenant is not None
    if settings.is_development:
        expected_compliance_role = _is_dev_compliance_subject(oidc_subject)
        if user.is_compliance_user != expected_compliance_role:
            # Correct users created by the previous development default while
            # preserving Carol as the explicit demo compliance role.
            user.is_compliance_user = expected_compliance_role
            await session.commit()
    return AuthenticatedUser(
        user_id=UUID(str(user.id)),
        tenant_id=UUID(str(tenant.id)),
        oidc_subject=user.oidc_subject,
        display_name=user.display_name,
        email=user.email,
        is_compliance_user=user.is_compliance_user,
    )
