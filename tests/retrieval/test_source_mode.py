from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.auth.identities import find_identity_link
from app.domain.types import Source
from app.retrieval.lexical import lexical_search
from app.retrieval.pipeline import _has_searchable_content
from app.retrieval.underfill import vector_search_with_fallback
from app.retrieval.vector import vector_search


class _EmptyScalars:
    def all(self) -> list[Any]:
        return []


class _EmptyResult:
    def all(self) -> list[Any]:
        return []

    def scalars(self) -> _EmptyScalars:
        return _EmptyScalars()

    def scalar_one_or_none(self) -> None:
        return None


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()


def _source_mode_module() -> Any:
    return importlib.import_module("app.domain.source_mode")


def _assert_connection_mode(statement: Any, expected: str) -> None:
    compiled = statement.compile()
    assert "JOIN source_connections" in str(compiled)
    assert "source_connections.mode" in str(compiled)
    assert expected in compiled.params.values()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("fixture", "fixture"), ("live", "delegated_oauth")],
)
def test_active_connection_mode_maps_runtime_mode(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: str,
) -> None:
    source_mode = _source_mode_module()
    monkeypatch.setattr(
        source_mode,
        "get_settings",
        lambda: SimpleNamespace(SOURCE_DATA_MODE=configured),
    )

    assert source_mode.active_connection_mode() == expected


async def test_identity_lookup_filters_the_active_connection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = importlib.import_module("app.auth.identities")
    monkeypatch.setattr(identities, "active_connection_mode", lambda: "delegated_oauth")
    session = _CapturingSession()

    await find_identity_link(session, uuid4(), Source.GOOGLE_DRIVE)  # type: ignore[arg-type]

    _assert_connection_mode(session.statements[0], "delegated_oauth")


async def test_vector_search_filters_the_active_connection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector = importlib.import_module("app.retrieval.vector")
    monkeypatch.setattr(vector, "active_connection_mode", lambda: "fixture")
    session = _CapturingSession()

    await vector_search(session, uuid4(), [0.1] * 1536)  # type: ignore[arg-type]

    _assert_connection_mode(session.statements[0], "fixture")


async def test_lexical_search_filters_the_active_connection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lexical = importlib.import_module("app.retrieval.lexical")
    monkeypatch.setattr(lexical, "active_connection_mode", lambda: "delegated_oauth")
    session = _CapturingSession()

    await lexical_search(session, uuid4(), "payment outage")  # type: ignore[arg-type]

    _assert_connection_mode(session.statements[0], "delegated_oauth")


async def test_exact_vector_fallback_filters_the_active_connection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    underfill = importlib.import_module("app.retrieval.underfill")
    monkeypatch.setattr(underfill, "active_connection_mode", lambda: "live-mode")

    async def no_index_hits(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(underfill, "vector_search", no_index_hits)
    session = _CapturingSession()

    await vector_search_with_fallback(session, uuid4(), [0.1] * 1536)  # type: ignore[arg-type]

    select_statement = next(
        statement for statement in session.statements if "SELECT" in str(statement)
    )
    _assert_connection_mode(select_statement, "live-mode")


async def test_searchable_content_check_filters_the_active_connection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = importlib.import_module("app.retrieval.pipeline")
    monkeypatch.setattr(pipeline, "active_connection_mode", lambda: "fixture")
    session = _CapturingSession()

    assert await _has_searchable_content(session, uuid4()) is False  # type: ignore[arg-type]

    _assert_connection_mode(session.statements[0], "fixture")
