from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.auth.oidc import AuthenticatedUser
from app.domain.source_connection_service import queue_connection_sync
from app.persistence.models import BackfillRun, SourceConnection


class _Session:
    def __init__(self, connection: SourceConnection | None) -> None:
        self.connection = connection
        self.added: list[Any] = []
        self.commits = 0

    async def scalar(self, statement: Any) -> SourceConnection | None:
        entity = getattr(statement, "column_descriptions", [{}])[0].get("entity")
        if entity is BackfillRun:
            return None
        return self.connection

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if isinstance(value, BackfillRun) and value.id is None:
                value.id = uuid4()

    async def commit(self) -> None:
        self.commits += 1


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        oidc_subject="alice",
        display_name="Alice",
        email="alice@example.test",
        is_compliance_user=False,
    )


def _connection(user: AuthenticatedUser) -> SourceConnection:
    return SourceConnection(
        id=uuid4(),
        tenant_id=user.tenant_id,
        source="google_drive",
        external_workspace_id="workspace",
        display_name="Workspace",
        status="connected",
    )


async def test_sync_publishes_exact_user_connection_and_progress_run() -> None:
    user = _user()
    connection = _connection(user)
    session = _Session(connection)
    published: list[tuple[str, str, str, str]] = []

    result = await queue_connection_sync(
        session,
        user,
        UUID(str(connection.id)),
        lambda *args: published.append(args),
    )

    run = session.added[0]
    assert result == {
        "connectionId": str(connection.id),
        "runId": str(run.id),
        "status": "queued",
    }
    assert published == [
        (str(connection.id), str(user.tenant_id), str(user.user_id), str(run.id))
    ]
    assert session.commits == 1
