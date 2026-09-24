#!/usr/bin/env python3
"""Seed synthetic fixtures for demo and testing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api" / "src"))


async def main() -> None:
    """Seed tenant A with alice, bob, compliance user, and all source objects."""
    from app.persistence.session import get_engine

    engine = get_engine()
    # TODO: implement fixture seeding — tenant, users, source connections,
    # source objects with scripted permission changes per Section 51.
    await engine.dispose()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
