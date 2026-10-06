"""Provider token refresh helpers used by query and ingestion paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.domain.types import Source
from app.settings import get_settings


def pack_tokens(
    access_token: str,
    *,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
) -> str:
    """Serialise provider tokens for encrypted-at-rest storage."""
    import json

    return json.dumps(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
        separators=(",", ":"),
    )


def unpack_tokens(value: str) -> dict[str, Any]:
    """Read encrypted token payloads, retaining compatibility with old rows."""
    import json

    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {"access_token": value, "refresh_token": None, "expires_at": None}
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        return {"access_token": value, "refresh_token": None, "expires_at": None}
    return payload


async def refresh_access_token(source: Source, refresh_token: str) -> dict[str, Any]:
    """Refresh a delegated access token without logging token responses."""
    settings = get_settings()
    if source in {Source.CONFLUENCE, Source.JIRA}:
        url = "https://auth.atlassian.com/oauth/token"
        response = await _post_token(
            url,
            json={
                "grant_type": "refresh_token",
                "client_id": settings.ATLASSIAN_CLIENT_ID,
                "client_secret": settings.ATLASSIAN_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
        )
    elif source == Source.GOOGLE_DRIVE:
        response = await _post_token(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
        )
    else:
        raise ValueError("SOURCE_CREDENTIAL_EXPIRED")

    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("SOURCE_CREDENTIAL_EXPIRED")
    expires_in = response.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(UTC) + timedelta(seconds=max(1, int(expires_in)))
    return {
        "access_token": access_token,
        "refresh_token": response.get("refresh_token") or refresh_token,
        "expires_at": expires_at,
    }


async def _post_token(
    url: str,
    *,
    json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=json, data=data)
    except httpx.HTTPError as exc:
        raise ValueError("SOURCE_CREDENTIAL_EXPIRED") from exc
    if response.status_code >= 400:
        raise TypeError("SOURCE_CREDENTIAL_EXPIRED")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("SOURCE_CREDENTIAL_EXPIRED") from exc
    if not isinstance(payload, dict):
        raise TypeError("SOURCE_CREDENTIAL_EXPIRED")
    return payload
