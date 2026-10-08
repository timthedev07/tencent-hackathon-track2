"""Session lifecycle checks shared by API and synchronous Celery wrappers."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_dispose_engine_resets_engine_and_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.persistence import session

    class FakeEngine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(session, "_engine", engine)
    monkeypatch.setattr(session, "_session_factory", object())

    await session.dispose_engine()

    assert engine.disposed
    assert session._engine is None
    assert session._session_factory is None
