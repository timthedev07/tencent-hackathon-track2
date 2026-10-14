from __future__ import annotations

import httpx
import pytest
import respx
from app.connectors import confluence
from app.connectors.confluence import _ATLASSIAN_OAUTH_SCOPES, get_atlassian_accessible_resources
from app.connectors.http import request
from app.domain.types import AccessOutcome, DelegatedCredential

_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"


@respx.mock
async def test_atlassian_resources_returns_cloud_id() -> None:
    route = respx.get(_RESOURCES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "cloud-123", "name": "Acme Cloud"}],
        )
    )

    resources = await get_atlassian_accessible_resources("access-token")

    assert route.called
    assert resources == [{"id": "cloud-123", "name": "Acme Cloud"}]


@respx.mock
async def test_atlassian_resources_rejects_expired_credentials() -> None:
    respx.get(_RESOURCES_URL).mock(return_value=httpx.Response(401))

    with pytest.raises(ValueError, match="SOURCE_CREDENTIAL_EXPIRED"):
        await get_atlassian_accessible_resources("expired-token")


def test_atlassian_oauth_requests_confluence_page_scope() -> None:
    assert "read:page:confluence" in _ATLASSIAN_OAUTH_SCOPES.split()


@respx.mock
async def test_provider_requests_accept_json() -> None:
    route = respx.get("https://example.test/pages").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    await request("GET", "https://example.test/pages", access_token="access-token")

    assert route.calls.last.request.headers["Accept"] == "application/json"


@respx.mock
async def test_confluence_access_check_uses_valid_page_parameters() -> None:
    route = respx.get(
        "https://api.atlassian.com/ex/confluence/cloud-123/wiki/api/v2/pages/1015989"
    ).mock(return_value=httpx.Response(200, json={"id": "1015989"}))
    credential = DelegatedCredential(
        source="confluence",
        access_token="access-token",
        provider_metadata={"cloud_id": "cloud-123"},
    )

    result = await confluence.ConfluenceConnector().check_access(credential, "1015989")

    assert result.outcome == AccessOutcome.ALLOW
    assert str(route.calls.last.request.url.params) == ""
