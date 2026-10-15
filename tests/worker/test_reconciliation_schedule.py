"""Check the timed demo cadence without waiting or contacting providers."""

import pickle
from datetime import UTC, datetime, timedelta

import pytest
from app.settings import Settings
from pydantic import ValidationError
from worker.reconciliation_schedule import ReconciliationSchedule

CUT_OFF = datetime.fromisoformat("2026-10-15T23:30:00+08:00")


@pytest.mark.parametrize(
    ("offset_seconds", "last_run_seconds_ago", "due", "next_check"),
    [
        (-90, 60, True, 60),
        (-30, 0, False, 30),
        (0, 60, False, 840),
        (840, 900, True, 900),
        (86400, 60, False, 840),
    ],
)
def test_interval_changes_at_the_singapore_cut_off(
    offset_seconds: int, last_run_seconds_ago: int, due: bool, next_check: int
) -> None:
    now = (CUT_OFF + timedelta(seconds=offset_seconds)).astimezone(UTC)
    cadence = ReconciliationSchedule(timedelta(minutes=1), CUT_OFF, nowfun=lambda: now)

    result = cadence.is_due(now - timedelta(seconds=last_run_seconds_ago))

    assert result.is_due is due
    assert result.next == next_check


def test_schedule_without_cut_off_retains_the_configured_interval() -> None:
    cadence = ReconciliationSchedule(timedelta(minutes=3), nowfun=lambda: CUT_OFF)

    result = cadence.is_due(CUT_OFF - timedelta(minutes=1))

    assert not result.is_due
    assert result.next == 120


def test_beat_persistence_retains_the_interval_change() -> None:
    cadence = ReconciliationSchedule(timedelta(minutes=1), CUT_OFF)

    restored = pickle.loads(pickle.dumps(cadence))  # noqa: S301 - locally serialised test data

    assert restored == cadence
    assert restored.change_at == CUT_OFF
    assert restored.after_interval == timedelta(minutes=15)


def test_settings_reject_a_cut_off_without_a_timezone() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"RECONCILIATION_INTERVAL_CHANGE_AT": "2026-10-15T23:30:00"})
