"""Noticing that the system stopped running.

The 2026-08-24 blackout is the case these are written against: twenty-one
trading days with nothing recorded, no error anywhere, found a month later by
a human reading timestamps. The property that matters is that a normal night
and a normal weekend are silent, and a real absence is not.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from scanner.execution import continuity

UTC = timezone.utc


def _at(y, m, d, hh=13, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def _log(tmp_path, stamps):
    path = tmp_path / "audit.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for stamp in stamps:
            handle.write(json.dumps({"event": "x", "at": stamp.isoformat()}))
            handle.write("\n")
    return path


# --- the quiet cases, which must stay quiet -----------------------------

def test_an_ordinary_overnight_gap_is_silent():
    """Seventeen hours, and entirely correct."""
    assert continuity.missed_sessions(_at(2026, 9, 24, 16), _at(2026, 9, 25, 9)) == 0


def test_an_ordinary_weekend_is_silent():
    """Friday afternoon to Monday morning is sixty-five hours and normal."""
    assert continuity.missed_sessions(_at(2026, 9, 25, 16), _at(2026, 9, 28, 9)) == 0


def test_a_long_weekend_is_silent():
    """Friday to Tuesday, with Monday a holiday. Over-counting a holiday by
    one is the accepted cost of not needing a broker call to decide."""
    assert continuity.missed_sessions(_at(2026, 9, 25, 16), _at(2026, 9, 29, 9)) == 1


def test_a_run_later_the_same_day_is_silent():
    assert continuity.missed_sessions(_at(2026, 9, 25, 9), _at(2026, 9, 25, 16)) == 0


# --- the loud cases ------------------------------------------------------

def test_a_single_missed_weekday_is_reported():
    """One missed session is a scheduler that did not fire. Hearing about it
    on day one is the whole point."""
    assert continuity.missed_sessions(_at(2026, 9, 23, 16), _at(2026, 9, 25, 9)) == 1


def test_the_real_blackout_is_counted():
    """2026-08-24 to 09-24. FREEZE.md records 21 trading days missed, counting
    Labor Day as a holiday; weekday arithmetic sees 22."""
    missed = continuity.missed_sessions(_at(2026, 8, 24, 11), _at(2026, 9, 24, 5))
    assert missed == 22


def test_a_gap_reports_days_and_sessions(tmp_path):
    path = _log(tmp_path, [_at(2026, 8, 24, 11)])
    gap = continuity.check(path, now=_at(2026, 9, 24, 5))
    assert gap is not None
    assert gap.is_alerting
    assert gap.missed_sessions == 22
    assert 30 < gap.days < 31
    assert "trading day(s) missed" in gap.describe()


def test_a_normal_gap_does_not_alert(tmp_path):
    path = _log(tmp_path, [_at(2026, 9, 24, 16)])
    gap = continuity.check(path, now=_at(2026, 9, 25, 9))
    assert gap is not None
    assert not gap.is_alerting


# --- never cry wolf ------------------------------------------------------

def test_the_latest_record_wins_not_the_last_line(tmp_path):
    """The log is append-only but not guaranteed sorted."""
    path = _log(tmp_path, [_at(2026, 9, 25, 9), _at(2026, 9, 24, 9)])
    gap = continuity.check(path, now=_at(2026, 9, 25, 12))
    assert gap is not None and gap.missed_sessions == 0


def test_a_first_run_is_not_a_gap(tmp_path):
    assert continuity.check(tmp_path / "nothing.jsonl") is None


def test_an_empty_log_is_not_a_gap(tmp_path):
    assert continuity.check(_log(tmp_path, [])) is None


def test_damaged_lines_are_skipped_not_fatal(tmp_path):
    path = _log(tmp_path, [_at(2026, 9, 25, 9)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ not json\n")
        handle.write(json.dumps({"event": "x", "at": "not-a-date"}) + "\n")
        handle.write(json.dumps({"event": "x"}) + "\n")
    gap = continuity.check(path, now=_at(2026, 9, 25, 12))
    assert gap is not None and gap.missed_sessions == 0


def test_a_clock_that_went_backwards_is_not_a_gap():
    assert continuity.missed_sessions(_at(2026, 9, 25, 16), _at(2026, 9, 25, 9)) == 0
