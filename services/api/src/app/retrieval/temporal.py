"""Shared bitemporal predicates for content, ACL, identity, and claim state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.sql.elements import ColumnElement


def active_at(model: Any, as_of: datetime) -> ColumnElement[bool]:
    """Return the valid-time and system-time visibility predicate at ``as_of``.

    Null boundaries are open-ended. Both clocks are constrained so a historical
    query cannot use a fact that Chronicle only observed later.
    """
    return and_(
        or_(model.valid_from.is_(None), model.valid_from <= as_of),
        or_(model.valid_to.is_(None), model.valid_to > as_of),
        model.observed_at <= as_of,
        or_(model.retired_at.is_(None), model.retired_at > as_of),
    )


def active_now(model: Any) -> ColumnElement[bool]:
    """Return the current local-state predicate for a bitemporal record."""
    now = func.now()
    return and_(
        or_(model.valid_from.is_(None), model.valid_from <= now),
        or_(model.valid_to.is_(None), model.valid_to > now),
        model.observed_at <= now,
        or_(model.retired_at.is_(None), model.retired_at > now),
    )
