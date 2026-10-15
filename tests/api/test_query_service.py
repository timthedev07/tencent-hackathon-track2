from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.auth.credentials import EncryptedPayload
from app.domain import query_service
from app.persistence.models import QueryRun


class _Session:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if isinstance(value, QueryRun):
                value.id = uuid4()
                value.status = "created"

    async def commit(self) -> None:
        self.commits += 1


class _SessionContext(AbstractAsyncContextManager[_Session]):
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


async def test_create_query_run_does_not_refresh_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    tenant_id = uuid4()
    user_id = uuid4()

    monkeypatch.setattr(query_service, "get_credential_manager", lambda: SimpleNamespace(
        encrypt_bytes=lambda value: EncryptedPayload(ciphertext=b"encrypted-query")
    ))
    monkeypatch.setattr(
        query_service,
        "get_session_factory",
        lambda: lambda: _SessionContext(session),
    )

    async def set_context(*_args: object) -> None:
        return None

    async def append_event(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(query_service, "set_tenant_context", set_context)
    monkeypatch.setattr(query_service, "append_query_event", append_event)

    result = await query_service.create_query_run(user_id, tenant_id, "payment outage")

    assert result == {"id": UUID(str(session.added[0].id)), "status": "created"}
    assert session.commits == 1
