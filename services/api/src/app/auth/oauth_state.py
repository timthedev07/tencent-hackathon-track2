"""Short-lived, signed OAuth state values.

OAuth state must bind the callback to the authenticated tenant and user without
putting a bearer token in the provider redirect URL.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from uuid import UUID

from app.settings import get_settings

_STATE_TTL_SECONDS = 600


def create_oauth_state(user_id: UUID, tenant_id: UUID) -> str:
    """Create an opaque, signed state value for an OAuth callback."""
    issued_at = str(int(time.time()))
    payload = f"{tenant_id}:{user_id}:{issued_at}".encode("ascii")
    signature = hmac.new(_state_key(), payload, hashlib.sha256).digest()
    encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def verify_oauth_state(state: str) -> tuple[UUID, UUID]:
    """Validate state freshness and integrity, returning tenant and user IDs."""
    try:
        encoded_payload, encoded_signature = state.split(".", 1)
        padding = "=" * (-len(encoded_payload) % 4)
        payload = base64.urlsafe_b64decode(f"{encoded_payload}{padding}")
        signature_padding = "=" * (-len(encoded_signature) % 4)
        signature = base64.urlsafe_b64decode(f"{encoded_signature}{signature_padding}")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("AUTH_INVALID_TOKEN") from exc

    expected = hmac.new(_state_key(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("AUTH_INVALID_TOKEN")
    try:
        tenant_text, user_text, issued_text = payload.decode("ascii").split(":")
        issued_at = int(issued_text)
        tenant_id = UUID(tenant_text)
        user_id = UUID(user_text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("AUTH_INVALID_TOKEN") from exc
    if abs(int(time.time()) - issued_at) > _STATE_TTL_SECONDS:
        raise ValueError("AUTH_INVALID_TOKEN")
    return tenant_id, user_id


def _state_key() -> bytes:
    secret = get_settings().ENCRYPTION_MASTER_KEY
    if not secret or secret == "dev-only-do-not-use-in-production-32bytes!!":
        if get_settings().is_production:
            raise ValueError("AUTH_CONFIGURATION_ERROR")
        secret = "development-oauth-state-key"
    return hashlib.sha256(secret.encode("utf-8")).digest()
