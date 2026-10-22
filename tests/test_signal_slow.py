from datetime import date, timedelta

from scanner.signals import slow


def days(n, start=date(2026, 10, 5)):          # a Monday
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d, 10.0, 11.0, 9.0, 10.0 + len(out), 100.0))
        d += timedelta(days=1)
    return out


def test_weekly_groups_by_iso_week_dated_by_last_day():
    w = slow.weekly(days(10))
    assert len(w) == 2 and w[0][0] == date(2026, 10, 9) and w[0][4] == 14.0 and w[0][5] == 500.0


def test_completed_candles_drop_the_forming_day_and_week():
    rows = days(13)                                  # through Wed 10-21
    assert slow.completed_daily(rows, date(2026, 10, 21))[-1][0] == date(2026, 10, 20)
    assert [w[0] for w in slow.completed_weekly(rows, date(2026, 10, 21))] == [date(2026, 10, 9), date(2026, 10, 16)]
