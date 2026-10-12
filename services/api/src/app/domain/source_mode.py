"""Runtime selection of fixture or delegated-OAuth source data."""

from __future__ import annotations

from typing import Literal

from app.settings import get_settings

type ConnectionMode = Literal["fixture", "delegated_oauth"]


def active_connection_mode() -> ConnectionMode:
    """Return the persisted connection mode eligible for this runtime."""
    if get_settings().SOURCE_DATA_MODE == "live":
        return "delegated_oauth"
    return "fixture"
