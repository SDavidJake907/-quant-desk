import json
from datetime import datetime, timedelta, timezone

from scanner.advisor import brief_store as bs

AK = timezone(timedelta(hours=-8))


def _write(d, day: str, written: datetime, text: str = "market") -> None:
    (d / f"{day}.json").write_text(json.dumps(
        {"date": day, "written_at": written.isoformat(), "market_text": text}), encoding="utf-8")


def test_todays_brief(tmp_path):
    now = datetime(2026, 10, 22, 13, 15, tzinfo=AK)
    _write(tmp_path, "2026-10-22", datetime(2026, 10, 22, 12, 50, tzinfo=AK))
    assert bs.latest(tmp_path, now)["date"] == "2026-10-22"


def test_after_midnight_the_evening_brief_still_counts(tmp_path):
    """01:15 AK: no brief for the new date yet; last night's 18:50 one is 6.5 h old."""
    now = datetime(2026, 10, 23, 1, 15, tzinfo=AK)
    _write(tmp_path, "2026-10-22", datetime(2026, 10, 22, 18, 50, tzinfo=AK))
    assert bs.latest(tmp_path, now)["date"] == "2026-10-22"


def test_too_old_is_refused(tmp_path):
    """Yesterday's news must never pass for today's: older than MAX_AGE -> None."""
    now = datetime(2026, 10, 23, 5, 45, tzinfo=AK)
    _write(tmp_path, "2026-10-22", datetime(2026, 10, 22, 12, 50, tzinfo=AK))
    assert bs.latest(tmp_path, now) is None


def test_newest_wins(tmp_path):
    now = datetime(2026, 10, 23, 3, 15, tzinfo=AK)
    _write(tmp_path, "2026-10-22", datetime(2026, 10, 22, 18, 50, tzinfo=AK), "evening")
    _write(tmp_path, "2026-10-23", datetime(2026, 10, 23, 3, 0, tzinfo=AK), "morning")
    assert bs.latest(tmp_path, now)["market_text"] == "morning"


def test_broken_or_empty_files_are_ignored(tmp_path):
    now = datetime(2026, 10, 22, 13, 15, tzinfo=AK)
    (tmp_path / "2026-10-22.json").write_text("{not json", encoding="utf-8")
    assert bs.latest(tmp_path, now) is None
    _write(tmp_path, "2026-10-22", datetime(2026, 10, 22, 12, 50, tzinfo=AK), text="")
    assert bs.latest(tmp_path, now) is None


def test_age_text():
    now = datetime(2026, 10, 23, 1, 15, tzinfo=AK)
    brief = {"written_at": datetime(2026, 10, 22, 18, 50, tzinfo=AK).isoformat()}
    assert bs.age_text(brief, now) == "written 6 h 25 min ago"
