"""Closure detection, and the December dates the Friday-only flatten misses.

The payloads here are the broker's real shape, taken from a live
`/v2/calendar` response rather than invented, so a change in how Alpaca
represents an early close fails these tests instead of passing them quietly.
"""

from __future__ import annotations

from datetime import date, time

from scanner.sources.market_calendar import (
    Session,
    closures,
    holidays,
    parse_sessions,
    unflattened,
)


def _row(day: str, close: str = "16:00", open_: str = "09:30") -> dict:
    """One calendar row in the broker's own shape."""
    return {
        "date": day, "open": open_, "close": close,
        "session_open": "0400", "session_close": "2000",
        "settlement_date": day,
    }


#: The real calendar across Christmas and New Year 2026, which is where both
#: hazard dates live. 12-25 and 01-01 are holidays; 12-24 is a half day.
DECEMBER = [
    _row("2026-12-21"), _row("2026-12-22"), _row("2026-12-23"),
    _row("2026-12-24", close="13:00"),
    _row("2026-12-28"), _row("2026-12-29"), _row("2026-12-30"),
    _row("2026-12-31"),
    _row("2027-01-04"), _row("2027-01-05"),
]

#: Thanksgiving 2026. Thursday is closed and Friday is a half day, which the
#: existing Friday-only flatten already covers — the contrast that shows the
#: December failure is about the weekday, not about early closes.
THANKSGIVING = [
    _row("2026-11-23"), _row("2026-11-24"), _row("2026-11-25"),
    _row("2026-11-27", close="13:00"),
    _row("2026-11-30"),
]


def test_parses_a_standard_session():
    sessions = parse_sessions([_row("2026-09-25")])
    assert sessions == (
        Session(on=date(2026, 9, 25), opens=time(9, 30), closes=time(16, 0)),
    )
    assert not sessions[0].is_early_close


def test_recognises_an_early_close():
    sessions = parse_sessions([_row("2026-12-24", close="13:00")])
    assert sessions[0].is_early_close


def test_missing_hours_are_treated_as_standard():
    """The broker omits the fields on an ordinary day rather than sending them."""
    sessions = parse_sessions([{"date": "2026-09-25"}])
    assert sessions[0].opens == time(9, 30)
    assert sessions[0].closes == time(16, 0)


def test_unparseable_rows_are_dropped_not_guessed():
    sessions = parse_sessions([
        {"date": "not-a-date"}, {"open": "09:30"}, _row("2026-09-25"),
    ])
    assert len(sessions) == 1


def test_sessions_come_back_sorted_and_deduplicated():
    sessions = parse_sessions([
        _row("2026-09-28"), _row("2026-09-25"), _row("2026-09-25"),
    ])
    assert [s.on for s in sessions] == [date(2026, 9, 25), date(2026, 9, 28)]


def test_holidays_are_weekdays_with_no_session():
    found = holidays(
        parse_sessions(DECEMBER), date(2026, 12, 21), date(2027, 1, 5),
    )
    assert date(2026, 12, 25) in found
    assert date(2027, 1, 1) in found


def test_weekends_are_not_reported_as_holidays():
    found = holidays(
        parse_sessions(DECEMBER), date(2026, 12, 21), date(2027, 1, 5),
    )
    assert all(day.weekday() < 5 for day in found)
    assert date(2026, 12, 26) not in found     # a Saturday


def test_an_ordinary_weekend_is_a_closure():
    """Three days, Friday to Monday. The flatten exists for exactly this."""
    found = closures(parse_sessions([_row("2026-09-25"), _row("2026-09-28")]))
    assert len(found) == 1
    assert found[0].days == 3
    assert found[0].last_session == date(2026, 9, 25)


def test_christmas_eve_is_a_four_day_closure_from_a_thursday():
    found = {c.last_session: c for c in closures(parse_sessions(DECEMBER))}
    christmas = found[date(2026, 12, 24)]
    assert christmas.days == 4
    assert christmas.reopens == date(2026, 12, 28)
    assert christmas.last_session.weekday() == 3          # Thursday
    assert christmas.is_early_close                       # and only half a day


def test_new_years_eve_is_a_four_day_closure_from_a_thursday():
    found = {c.last_session: c for c in closures(parse_sessions(DECEMBER))}
    new_year = found[date(2026, 12, 31)]
    assert new_year.days == 4
    assert new_year.reopens == date(2027, 1, 4)
    assert new_year.last_session.weekday() == 3
    assert not new_year.is_early_close


def test_thanksgiving_closure_falls_on_a_friday_and_is_covered():
    """The Friday-only flatten handles this week; it is the December weeks
    that break, so this is the control case rather than a second hazard."""
    found = closures(parse_sessions(THANKSGIVING))
    assert [c.last_session for c in found] == [date(2026, 11, 27)]
    assert found[0].last_session.weekday() == 4           # Friday
    assert unflattened(parse_sessions(THANKSGIVING)) == ()


def test_both_december_thursdays_are_missed_by_a_friday_only_flatten():
    """THE HAZARD. Entries are allowed on Thursday and no flatten runs, so the
    book goes into a four-day close unprotected. Pinned here so the fix in
    WORKLIST section 10 has a failing case to satisfy."""
    missed = [c.last_session for c in unflattened(parse_sessions(DECEMBER))]
    assert missed == [date(2026, 12, 24), date(2026, 12, 31)]


def test_ordinary_weeks_leave_nothing_for_a_human_to_do():
    ordinary = parse_sessions([
        _row("2026-09-21"), _row("2026-09-22"), _row("2026-09-23"),
        _row("2026-09-24"), _row("2026-09-25"), _row("2026-09-28"),
    ])
    assert unflattened(ordinary) == ()
