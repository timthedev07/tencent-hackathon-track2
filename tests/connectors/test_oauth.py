from __future__ import annotations

import httpx
import pytest
import respx
from app.connectors.oauth import refresh_access_token
from app.domain.types import Source


@respx.mock
async def test_google_refresh_rejects_expired_refresh_token_as_value_error() -> None:
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "Token has been expired"},
        )
    )

    with pytest.raises(ValueError, match="SOURCE_CREDENTIAL_EXPIRED"):
        await refresh_access_token(Source.GOOGLE_DRIVE, "expired-refresh-token")


@respx.mock
async def test_google_refresh_rejects_malformed_token_response_as_value_error() -> None:
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json=["not", "an", "object"])
    )

    with pytest.raises(ValueError, match="SOURCE_CREDENTIAL_EXPIRED"):
        await refresh_access_token(Source.GOOGLE_DRIVE, "refresh-token")
