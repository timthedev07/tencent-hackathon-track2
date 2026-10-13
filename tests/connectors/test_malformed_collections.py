from __future__ import annotations

from collections.abc import Awaitable, Callable
from http import HTTPStatus
from uuid import UUID

import httpx
import pytest
from app.connectors import confluence, google_drive, jira, slack
from app.connectors.errors import ForbiddenError
from app.domain.types import DelegatedCredential, Source, SourceConnection

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
CONNECTION_ID = UUID("00000000-0000-0000-0000-000000000002")


def _connection(source: Source) -> SourceConnection:
    return SourceConnection(
        id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        source=source,
        external_workspace_id="workspace",
    )


def _credential(source: Source) -> DelegatedCredential:
    return DelegatedCredential(
        source=source,
        access_token="test-token",
        provider_metadata={"cloud_id": "cloud"},
    )


def _request_returning(
    payload: dict[str, object],
) -> Callable[..., Awaitable[httpx.Response]]:
    async def fake_request(*args: object, **kwargs: object) -> httpx.Response:
        return httpx.Response(HTTPStatus.OK, json=payload)

    return fake_request


async def test_confluence_scan_ignores_non_list_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(confluence, "request", _request_returning({"results": None}))
    page = await confluence.ConfluenceConnector().initial_scan(
        _connection(Source.CONFLUENCE), None, _credential(Source.CONFLUENCE)
    )
    assert page.objects == []


async def test_confluence_acl_ignores_non_list_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(confluence, "request", _request_returning({"results": None}))
    result = await confluence.ConfluenceConnector().fetch_acl(
        _connection(Source.CONFLUENCE), "page-1", _credential(Source.CONFLUENCE)
    )
    assert result.grants == []


async def test_confluence_acl_failure_does_not_discard_fetched_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rejected_request(*args: object, **kwargs: object) -> httpx.Response:
        raise ForbiddenError("Provider access forbidden")

    monkeypatch.setattr(confluence, "request", rejected_request)
    result = await confluence.ConfluenceConnector().fetch_acl(
        _connection(Source.CONFLUENCE), "page-1", _credential(Source.CONFLUENCE)
    )
    assert result.outcome.value == "success"
    assert result.grants == []


async def test_jira_scan_ignores_non_list_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jira, "request", _request_returning({"issues": None}))
    page = await jira.JiraConnector().initial_scan(
        _connection(Source.JIRA), None, _credential(Source.JIRA)
    )
    assert page.objects == []


async def test_jira_scan_uses_bounded_jql(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_request(*args: object, **kwargs: object) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"issues": []})

    monkeypatch.setattr(jira, "request", fake_request)
    await jira.JiraConnector().initial_scan(
        _connection(Source.JIRA), None, _credential(Source.JIRA)
    )

    assert captured["params"]["jql"] == "project IS NOT EMPTY ORDER BY updated ASC"  # type: ignore[index]


async def test_drive_scan_ignores_non_list_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_drive, "request", _request_returning({"files": None}))
    page = await google_drive.GoogleDriveConnector().initial_scan(
        _connection(Source.GOOGLE_DRIVE), None, _credential(Source.GOOGLE_DRIVE)
    )
    assert page.objects == []


async def test_drive_scan_skips_unextractable_file_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        google_drive,
        "request",
        _request_returning(
            {
                "files": [
                    {"id": "doc", "mimeType": "application/vnd.google-apps.document"},
                    {"id": "video", "mimeType": "video/mp4"},
                ]
            }
        ),
    )

    page = await google_drive.GoogleDriveConnector().initial_scan(
        _connection(Source.GOOGLE_DRIVE), None, _credential(Source.GOOGLE_DRIVE)
    )

    assert [item["external_object_id"] for item in page.objects] == ["doc"]


async def test_drive_permissions_omit_empty_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_request(*args: object, **kwargs: object) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"permissions": []})

    monkeypatch.setattr(google_drive, "request", fake_request)
    await google_drive._permissions_for_file(_credential(Source.GOOGLE_DRIVE), "file-1")

    assert "pageToken" not in captured["params"]  # type: ignore[operator]


async def test_drive_initial_scan_omits_empty_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_request(*args: object, **kwargs: object) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"files": []})

    monkeypatch.setattr(google_drive, "request", fake_request)
    await google_drive.GoogleDriveConnector().initial_scan(
        _connection(Source.GOOGLE_DRIVE), None, _credential(Source.GOOGLE_DRIVE)
    )

    assert "pageToken" not in captured["params"]  # type: ignore[operator]


async def test_drive_changes_ignore_non_list_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_drive, "request", _request_returning({"changes": None}))
    page = await google_drive.GoogleDriveConnector().read_changes(
        _connection(Source.GOOGLE_DRIVE), "cursor", _credential(Source.GOOGLE_DRIVE)
    )
    assert page.changes == []


async def test_slack_scan_ignores_non_list_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack, "request", _request_returning({"ok": True, "channels": None}))
    page = await slack.SlackConnector().initial_scan(
        _connection(Source.SLACK), None, _credential(Source.SLACK)
    )
    assert page.objects == []
