import pytest
from datetime import datetime
from core.publisher_engine import compute_next_run


def test_compute_next_run_interval():
    from_dt = datetime(2026, 8, 16, 10, 0, 0)
    rule = {"freq": "interval", "interval_hours": 6}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 16, 16, 0, 0)


def test_compute_next_run_daily_future_today():
    from_dt = datetime(2026, 8, 16, 8, 0, 0)
    rule = {"freq": "daily", "time_hhmm": "14:30"}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 16, 14, 30, 0)


def test_compute_next_run_daily_past_today():
    from_dt = datetime(2026, 8, 16, 15, 0, 0)
    rule = {"freq": "daily", "time_hhmm": "09:00"}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 17, 9, 0, 0)


def test_compute_next_run_weekly():
    # 2026-08-16 is Sunday (weekday 6)
    from_dt = datetime(2026, 8, 16, 12, 0, 0)
    # Next Monday (weekday 0) at 10:00
    rule = {"freq": "weekly", "weekday": 0, "time_hhmm": "10:00"}
    next_dt = compute_next_run(rule, from_dt)
    assert next_dt == datetime(2026, 8, 17, 10, 0, 0)


def test_compute_next_run_none():
    assert compute_next_run(None) is None
    assert compute_next_run({}) is None
