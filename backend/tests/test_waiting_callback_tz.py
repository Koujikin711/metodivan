"""Тик «В ожидании» не должен падать на naive scheduled_at из БД."""

from datetime import UTC, datetime

from app.services.waiting_callback_reminders import _as_utc, _fmt_dt


def test_as_utc_accepts_naive_and_aware():
    now = datetime.now(UTC)
    naive = datetime(2026, 11, 27, 9, 0, 0)
    aware = datetime(2026, 11, 27, 9, 0, 0, tzinfo=UTC)
    assert (_as_utc(naive) - now).total_seconds() != 0 or True
    assert (_as_utc(aware) - now).total_seconds() != 0 or True
    assert _as_utc(naive).tzinfo is not None
    assert _as_utc(aware) == aware


def test_fmt_dt_naive_scheduled():
    date_s, time_s = _fmt_dt(datetime(2026, 11, 27, 9, 0, 0))  # UTC naive → +05 Душанбе
    assert date_s == "27.11.2026"
    assert time_s == "14:00"
