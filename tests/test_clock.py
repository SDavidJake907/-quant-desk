"""The "Now:" line at the top of Gemma's question (advisor/clock.py)."""

from datetime import date, datetime, timedelta

from scanner.advisor import clock

HOLIDAYS = {date(2026, 11, 26), date(2026, 12, 25), date(2027, 1, 1)}
EARLY = {date(2026, 11, 27), date(2026, 12, 24)}


def rows(start=date(2026, 10, 20), end=date(2027, 1, 8)):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append({"date": d.isoformat(), "open": "09:30",
                        "close": "13:00" if d in EARLY else "16:00"})
        d += timedelta(days=1)
    return out


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=clock.ET)


def test_friday_evening_names_the_weekend():
    line = clock.now_line(at("2026-10-23T19:16"), rows(), "after")
    assert line.startswith("Now: Friday 2026-10-23, 7:16 PM ET (after-hours).")
    assert "Next regular session: Monday 10-26, 9:30 AM ET; closed Saturday and Sunday." in line
    assert "October: 5 trading days left; the monthly verdict is Oct 31." in line
    assert "Q4: 47 trading days left (ends Dec 31)." in line


def test_regular_hours_show_time_left_and_count_today():
    line = clock.now_line(at("2026-10-23T14:15"), rows(), "regular")
    assert "Regular session closes 4:00 PM ET, 1h 45m left." in line
    assert "October: 6 trading days left" in line
    assert "Next regular session" not in line


def test_early_close_and_holiday():
    assert "closes 1:00 PM ET (early close), 2h 00m left" in clock.now_line(
        at("2026-11-27T11:00"), rows(), "regular")
    line = clock.now_line(at("2026-11-25T18:00"), rows(), "after")
    assert "Friday 11-27, 9:30 AM ET, closes early 1:00 PM ET; closed Thursday (holiday)." in line


def test_premarket_says_the_open_once():
    line = clock.now_line(at("2026-10-26T08:00"), rows(), "pre")
    assert "Regular session opens 9:30 AM ET, in 1h 30m." in line
    assert "Next regular session" not in line


def test_year_end():
    line = clock.now_line(at("2026-12-31T17:00"), rows(), "after")
    assert "Monday 01-04" in line and "closed Friday (holiday), Saturday and Sunday" in line
    assert "December: 0 trading days left" in line
