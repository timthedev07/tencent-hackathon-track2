from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.domain.types import CompiledContext, GeneratedAnswer
from app.models import gateway
from app.persistence.models import QueryRun
from app.retrieval import pipeline


class _SingleResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _AuditSession:
    def __init__(self, run: QueryRun) -> None:
        self.run = run
        self.added: list[Any] = []

    async def execute(self, statement: Any) -> _SingleResult:
        return _SingleResult(self.run)

    def add(self, value: Any) -> None:
        self.added.append(value)


def test_stub_gateway_reports_local_execution_versions() -> None:
    model_gateway = gateway.StubModelGateway()

    assert model_gateway.generation_model_version == "stub-extractive-v1"
    assert model_gateway.embedding_model_version == "deterministic-local-v1"


def test_http_gateway_reports_configured_execution_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gateway,
        "get_settings",
        lambda: SimpleNamespace(
            EMBEDDING_DIMENSION=1536,
            GENERATION_MODEL="configured-generation",
            EMBEDDING_MODEL="configured-embedding",
        ),
    )

    model_gateway = gateway.HttpModelGateway()

    assert model_gateway.generation_model_version == "configured-generation"
    assert model_gateway.embedding_model_version == "configured-embedding"


async def test_audit_commit_stores_supplied_gateway_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    run = QueryRun(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        query_ciphertext=b"query",
        status="created",
        started_at=now,
    )
    session = _AuditSession(run)

    class _CredentialManager:
        def encrypt_bytes(self, value: bytes) -> SimpleNamespace:
            return SimpleNamespace(ciphertext=b"encrypted")

    async def no_op(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(pipeline, "get_credential_manager", _CredentialManager)
    monkeypatch.setattr(pipeline, "append_event", no_op)
    monkeypatch.setattr(pipeline, "append_query_event", no_op)

    await pipeline._audit_commit(
        session=session,  # type: ignore[arg-type]
        query_id=uuid4(),
        tenant_id=uuid4(),
        answer=GeneratedAnswer(answer="No evidence.", claims=[]),
        reranked=[],
        access_decisions={},
        object_source_map={},
        status="denied",
        compiled_context=CompiledContext(
            evidence_blocks=[],
            system_prompt="System",
            total_token_count=0,
            snapshot_hash="snapshot",
        ),
        model_version="stub-extractive-v1",
        embedding_version="deterministic-local-v1",
    )

    assert run.model_version == "stub-extractive-v1"
    assert run.embedding_version == "deterministic-local-v1"
