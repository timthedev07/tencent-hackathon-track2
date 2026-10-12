from __future__ import annotations

import asyncio
import importlib
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.dependencies.auth import get_authenticated_user
from app.api.routes import oauth
from app.auth.oidc import AuthenticatedUser
from app.persistence.models import SourceConnection
from app.persistence.session import get_session
from fastapi import FastAPI, HTTPException


class _ScalarRows:
    def __init__(self, rows: list[SourceConnection]) -> None:
        self._rows = rows

    def all(self) -> list[SourceConnection]:
        return self._rows


class _Session:
    def __init__(
        self,
        *,
        listed: list[SourceConnection] | None = None,
        selected: SourceConnection | None = None,
    ) -> None:
        self.listed = listed or []
        self.selected = selected
        self.statements: list[Any] = []

    async def scalars(self, statement: Any) -> _ScalarRows:
        self.statements.append(statement)
        return _ScalarRows(self.listed)

    async def scalar(self, statement: Any) -> SourceConnection | None:
        self.statements.append(statement)
        return self.selected


def _service() -> Any:
    return importlib.import_module("app.domain.source_connection_service")


def _actor() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        oidc_subject="alice",
        display_name="Alice",
        email="alice@example.test",
        is_compliance_user=False,
    )


def _connection(
    actor: AuthenticatedUser,
    *,
    mode: str = "delegated_oauth",
    source: str = "google_drive",
) -> SourceConnection:
    return SourceConnection(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        source=source,
        external_workspace_id=f"workspace-{source}",
        display_name="Real workspace" if mode == "delegated_oauth" else "Demo fixture",
        status="connected",
        mode=mode,
    )


def _do_not_publish(connection_id: str, tenant_id: str) -> None:
    return None


async def test_list_user_connections_filters_active_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    monkeypatch.setattr(service, "active_connection_mode", lambda: "delegated_oauth")
    actor = _actor()
    connection = _connection(actor)
    session = _Session(listed=[connection])

    summaries = await service.list_user_connections(session, actor)

    assert summaries == [
        service.SourceConnectionSummary(
            connection_id=UUID(str(connection.id)),
            source="google_drive",
            display_name="Real workspace",
            mode="delegated_oauth",
        )
    ]
    compiled = session.statements[0].compile()
    assert "identity_links.user_id" in str(compiled)
    assert "source_connections.tenant_id" in str(compiled)
    assert "source_connections.mode" in str(compiled)
    assert "delegated_oauth" in compiled.params.values()


@pytest.mark.parametrize(
    ("data_mode", "connection_mode", "can_sync"),
    [("fixture", "fixture", False), ("live", "delegated_oauth", True)],
)
async def test_connection_route_exposes_mode_and_sync_capability(
    monkeypatch: pytest.MonkeyPatch,
    data_mode: str,
    connection_mode: str,
    can_sync: bool,
) -> None:
    service = _service()
    actor = _actor()
    connection = _connection(actor, mode=connection_mode)

    async def summaries(*args: Any, **kwargs: Any) -> list[Any]:
        return [
            service.SourceConnectionSummary(
                connection_id=UUID(str(connection.id)),
                source="google_drive",
                display_name=connection.display_name,
                mode=connection_mode,
            )
        ]

    monkeypatch.setattr(oauth, "list_user_connections", summaries, raising=False)
    monkeypatch.setattr(
        oauth,
        "get_settings",
        lambda: SimpleNamespace(SOURCE_DATA_MODE=data_mode),
        raising=False,
    )

    response = await oauth.list_connections(actor, _Session())  # type: ignore[arg-type]
    drive = next(item for item in response.connections if item.source == "google_drive")

    assert response.dataMode == data_mode
    assert drive.connectionId == str(connection.id)
    assert drive.mode == connection_mode
    assert drive.canSync is can_sync


async def test_connection_route_fails_closed_for_ambiguous_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    actor = _actor()
    first = _connection(actor)
    second = _connection(actor)

    async def summaries(*args: Any, **kwargs: Any) -> list[Any]:
        return [
            service.SourceConnectionSummary(
                connection_id=UUID(str(item.id)),
                source="google_drive",
                display_name=item.display_name,
                mode="delegated_oauth",
            )
            for item in (first, second)
        ]

    monkeypatch.setattr(oauth, "list_user_connections", summaries)
    monkeypatch.setattr(
        oauth,
        "get_settings",
        lambda: SimpleNamespace(SOURCE_DATA_MODE="live"),
    )

    response = await oauth.list_connections(actor, _Session())  # type: ignore[arg-type]
    drive = next(item for item in response.connections if item.source == "google_drive")

    assert drive.connected is False
    assert drive.connectionId is None
    assert drive.canSync is False


async def test_queue_sync_requires_exact_owned_connection() -> None:
    service = _service()
    actor = _actor()
    session = _Session(selected=None)

    with pytest.raises(service.SourceConnectionNotFoundError):
        await service.queue_connection_sync(session, actor, uuid4(), _do_not_publish)

    compiled = session.statements[0].compile()
    sql = str(compiled)
    assert "identity_links.user_id" in sql
    assert "source_connections.tenant_id" in sql
    assert actor.user_id in compiled.params.values()
    assert actor.tenant_id in compiled.params.values()


async def test_queue_sync_rejects_fixture_connection() -> None:
    service = _service()
    actor = _actor()
    session = _Session(selected=_connection(actor, mode="fixture"))

    with pytest.raises(service.SourceConnectionNotSyncableError):
        await service.queue_connection_sync(session, actor, uuid4(), _do_not_publish)


async def test_queue_sync_publishes_only_connection_and_tenant_ids() -> None:
    service = _service()
    actor = _actor()
    connection = _connection(actor)
    session = _Session(selected=connection)
    published: list[tuple[str, str]] = []

    def record_publish(connection_id: str, tenant_id: str) -> None:
        published.append((connection_id, tenant_id))

    result = await service.queue_connection_sync(
        session,
        actor,
        UUID(str(connection.id)),
        record_publish,
    )

    assert result == {
        "connectionId": str(connection.id),
        "status": "queued",
    }
    assert published == [(str(connection.id), str(actor.tenant_id))]


async def test_queue_sync_wraps_broker_failure() -> None:
    service = _service()
    actor = _actor()
    connection = _connection(actor)

    def fail_publish(connection_id: str, tenant_id: str) -> None:
        raise RuntimeError("broker details must stay private")

    with pytest.raises(service.SourceSyncUnavailableError, match="SOURCE_SYNC_FAILED"):
        await service.queue_connection_sync(
            _Session(selected=connection),
            actor,
            UUID(str(connection.id)),
            fail_publish,
        )


async def test_queue_sync_does_not_block_the_event_loop() -> None:
    service = _service()
    actor = _actor()
    connection = _connection(actor)
    started = threading.Event()
    release = threading.Event()

    def wait_for_release(connection_id: str, tenant_id: str) -> None:
        started.set()
        release.wait(timeout=1)

    task = asyncio.create_task(
        service.queue_connection_sync(
            _Session(selected=connection),
            actor,
            UUID(str(connection.id)),
            wait_for_release,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0.01)
        if started.is_set():
            break
    was_pending_while_publisher_waited = not task.done()
    release.set()
    await task

    assert started.is_set()
    assert was_pending_while_publisher_waited


def test_backfill_publisher_uses_exact_ids_and_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    backfill = importlib.import_module("worker.tasks.backfill")
    published: list[dict[str, Any]] = []

    def record_publish(*args: Any, **kwargs: Any) -> None:
        published.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(backfill.run_backfill, "apply_async", record_publish)

    service.publish_source_backfill("connection-id", "tenant-id")

    assert published == [
        {
            "args": (),
            "kwargs": {
                "args": ["connection-id", "tenant-id"],
                "queue": "backfill",
                "retry": False,
            },
        }
    ]


def _route_app(actor: AuthenticatedUser, session: _Session) -> FastAPI:
    app = FastAPI()
    app.include_router(oauth.router, prefix="/v1/oauth")
    app.dependency_overrides[get_authenticated_user] = lambda: actor
    app.dependency_overrides[get_session] = lambda: session
    return app


@pytest.mark.parametrize(
    ("error_name", "expected_status", "expected_code"),
    [
        ("SourceConnectionNotFoundError", 404, "SOURCE_CONNECTION_NOT_FOUND"),
        ("SourceConnectionNotSyncableError", 409, "SOURCE_CONNECTION_NOT_SYNCABLE"),
        ("SourceSyncUnavailableError", 503, "SOURCE_SYNC_FAILED"),
    ],
)
async def test_sync_route_returns_safe_structured_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_name: str,
    expected_status: int,
    expected_code: str,
) -> None:
    service = _service()
    error_type = getattr(service, error_name)

    async def fail_sync(*args: Any, **kwargs: Any) -> dict[str, str]:
        raise error_type(expected_code)

    monkeypatch.setattr(oauth, "queue_connection_sync", fail_sync)
    actor = _actor()
    app = _route_app(actor, _Session())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(f"/v1/oauth/connections/{uuid4()}/sync")

    assert response.status_code == expected_status
    assert response.json()["detail"]["error"]["code"] == expected_code
    assert "broker details" not in response.text


@pytest.mark.parametrize(
    "start",
    [oauth.start_atlassian_oauth, oauth.start_slack_oauth, oauth.start_google_oauth],
)
async def test_oauth_start_is_blocked_in_fixture_mode(
    monkeypatch: pytest.MonkeyPatch,
    start: Callable[..., Any],
) -> None:
    monkeypatch.setattr(oauth, "active_connection_mode", lambda: "fixture", raising=False)

    with pytest.raises(HTTPException) as caught:
        await start(_actor())

    assert getattr(caught.value, "status_code", None) == 409
    detail = caught.value.detail
    assert isinstance(detail, dict)
    error = detail.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == "LIVE_MODE_REQUIRED"
