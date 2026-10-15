from __future__ import annotations

import httpx
from app.main import create_app
from fastapi import FastAPI


async def test_cors_allows_google_drive_scope_put() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            "/v1/oauth/connections/00000000-0000-0000-0000-000000000000/google-drive-scope",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]


async def test_cors_headers_are_present_on_unhandled_server_errors() -> None:
    app = create_app()
    inner_app = app.app  # type: ignore[attr-defined]
    assert isinstance(inner_app, FastAPI)

    @inner_app.get("/_test/cors-server-error")
    async def raise_server_error() -> None:
        raise RuntimeError("test exception")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/_test/cors-server-error",
            headers={"Origin": "http://localhost:3000"},
        )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
