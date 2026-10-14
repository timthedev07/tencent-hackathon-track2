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
        scope_config=(
            {"allowed_root_folder_ids": ["root-folder"]}
            if source == Source.GOOGLE_DRIVE
            else {}
        ),
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


def test_drive_folder_link_parser_accepts_drive_url_shapes() -> None:
    assert google_drive.parse_folder_id(
        "https://drive.google.com/drive/folders/folder-123?usp=sharing"
    ) == "folder-123"
    assert google_drive.parse_folder_id(
        "https://drive.google.com/drive/u/0/folders/folder_456"
    ) == "folder_456"
    assert google_drive.parse_folder_id(
        "https://drive.google.com/open?id=folder789"
    ) == "folder789"
    assert google_drive.parse_folder_id("folder-raw") == "folder-raw"


def test_drive_folder_link_parser_rejects_non_drive_urls() -> None:
    with pytest.raises(ValueError, match="secure Google Drive"):
        google_drive.parse_folder_id("https://example.com/folders/folder-123")


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


async def test_drive_folder_names_reads_visible_folder_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        assert method == "GET"
        if url.endswith("root-folder"):
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "id": "root-folder",
                    "name": "Payment Reliability",
                    "mimeType": "application/vnd.google-apps.folder",
                },
            )
        raise ForbiddenError("Provider access forbidden")

    monkeypatch.setattr(google_drive, "request", fake_request)
    names = await google_drive.GoogleDriveConnector().folder_names(
        ["root-folder", "restricted-folder"],
        _credential(Source.GOOGLE_DRIVE),
    )

    assert names == {"root-folder": "Payment Reliability"}


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


async def test_drive_scan_is_limited_to_scoped_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_request(*args: object, **kwargs: object) -> httpx.Response:
        captured.append(kwargs["params"])
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "files": [
                    {
                        "id": "nested-folder",
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                    {
                        "id": "postmortem",
                        "name": "Payment Gateway — Postmortem",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["root-folder"],
                    },
                ]
            },
        )

    monkeypatch.setattr(google_drive, "request", fake_request)
    page = await google_drive.GoogleDriveConnector().initial_scan(
        _connection(Source.GOOGLE_DRIVE), None, _credential(Source.GOOGLE_DRIVE)
    )

    assert captured[0]["q"] == "'root-folder' in parents and trashed = false"
    assert [item["external_object_id"] for item in page.objects] == ["postmortem"]
    assert page.objects[0]["provider_metadata"] == {
        "drive_id": None,
        "parent_ids": ["root-folder"],
        "scope_root_ids": ["root-folder"],
    }


async def test_drive_scan_discovers_nested_folder_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    async def fake_request(*args: object, **kwargs: object) -> httpx.Response:
        query = kwargs["params"]["q"]
        requests.append(str(query))
        if query.startswith("'root-folder'"):
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "files": [
                        {
                            "id": "nested-folder",
                            "mimeType": "application/vnd.google-apps.folder",
                        }
                    ]
                },
            )
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "files": [
                    {
                        "id": "nested-doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["nested-folder"],
                    }
                ]
            },
        )

    monkeypatch.setattr(google_drive, "request", fake_request)
    page = await google_drive.GoogleDriveConnector().initial_scan(
        _connection(Source.GOOGLE_DRIVE), None, _credential(Source.GOOGLE_DRIVE)
    )

    assert requests == [
        "'root-folder' in parents and trashed = false",
        "'nested-folder' in parents and trashed = false",
    ]
    assert page.objects[0]["external_object_id"] == "nested-doc"
    assert page.objects[0]["provider_metadata"]["scope_root_ids"] == ["root-folder"]  # type: ignore[index]


async def test_drive_scan_fails_closed_without_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_request(*args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError("unscoped Drive scan must not call the provider")

    connection = SourceConnection(
        id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        source=Source.GOOGLE_DRIVE,
        external_workspace_id="workspace",
    )
    monkeypatch.setattr(google_drive, "request", unexpected_request)
    page = await google_drive.GoogleDriveConnector().initial_scan(
        connection, None, _credential(Source.GOOGLE_DRIVE)
    )

    assert page.objects == []
    assert page.next_cursor is None


async def test_drive_changes_drop_files_moved_out_of_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(*args: object, **kwargs: object) -> httpx.Response:
        if "changes" in str(args[1]):
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "changes": [
                        {
                            "file": {
                                "id": "scoped-doc",
                                "mimeType": "application/vnd.google-apps.document",
                                "parents": ["root-folder"],
                            }
                        },
                        {
                            "file": {
                                "id": "personal-doc",
                                "mimeType": "application/vnd.google-apps.document",
                                "parents": ["personal-folder"],
                            }
                        },
                    ],
                    "newStartPageToken": "next-cursor",
                },
            )
        return httpx.Response(HTTPStatus.OK, json={"files": []})

    monkeypatch.setattr(google_drive, "request", fake_request)
    page = await google_drive.GoogleDriveConnector().read_changes(
        _connection(Source.GOOGLE_DRIVE), "cursor", _credential(Source.GOOGLE_DRIVE)
    )

    assert page.next_cursor == "next-cursor"
    assert page.changes[0]["external_object_id"] == "scoped-doc"
    assert page.changes[0]["provider_metadata"]["scope_root_ids"] == ["root-folder"]  # type: ignore[index]
    assert page.changes[1] == {
        "external_object_id": "personal-doc",
        "change_type": "deleted",
    }
    assert page.has_deletions is True


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
