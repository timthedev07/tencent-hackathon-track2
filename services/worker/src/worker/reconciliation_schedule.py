"""Reconciliation cadence with an optional dated interval change."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

from celery import Celery
from celery.schedules import schedstate, schedule


class ReconciliationSchedule(schedule):
    """Select the active interval on every Beat check, including after restarts."""

    __hash__ = None

    def __init__(
        self,
        run_every: timedelta,
        change_at: datetime | None = None,
        after_interval: timedelta = timedelta(minutes=15),
        nowfun: Callable[[], datetime] | None = None,
        app: Celery | None = None,
    ) -> None:
        if change_at is not None and change_at.utcoffset() is None:
            raise ValueError("The reconciliation change timestamp must include a timezone")
        self.change_at = change_at
        self.after_interval = after_interval
        super().__init__(run_every=run_every, nowfun=nowfun, app=app)

    def is_due(self, last_run_at: datetime) -> schedstate:
        """Re-evaluate at the cut-off before publishing another task."""
        now = self.now()
        after_change = self.change_at is not None and now >= self.change_at
        interval = self.after_interval if after_change else self.run_every
        active = schedule(run_every=interval, nowfun=lambda: now, app=self.app)
        # Celery incorrectly annotates the numeric delay as a datetime.
        is_due, next_check = cast(tuple[bool, float], active.is_due(last_run_at))
        if self.change_at is not None and not after_change:
            next_check = min(next_check, (self.change_at - now).total_seconds())
        return schedstate(is_due, next_check)

    def __reduce__(self) -> tuple:
        """Preserve the cut-off when Beat persists its schedule."""
        return type(self), (self.run_every, self.change_at, self.after_interval, self.nowfun)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReconciliationSchedule):
            return False
        return (
            self.run_every == other.run_every
            and self.change_at == other.change_at
            and self.after_interval == other.after_interval
        )
