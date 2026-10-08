"""Event-calendar tests.

This is the one addition to the strategy that removes a known risk rather than
adding an unvalidated signal. The property that matters most is that a failure
to read the calendar is never mistaken for a clean calendar — silently trading
through earnings because an API was down is worse than not checking at all.
"""

from __future__ import annotations

from datetime import date

from scanner.sources.calendar import (
    Earnings,
    _session_days,
    is_market_moving,
    parse_earnings,
)

WANTED = frozenset({"LITE", "AMD"})


def _payload(rows):
    return {"data": {"rows": rows}}


def test_parses_a_reporting_company():
    found = parse_earnings(
        _payload([{"symbol": "LITE", "time": "time-after-hours"}]),
        date(2026, 8, 11), WANTED,
    )
    assert found["LITE"].when == "after-close"
    assert found["LITE"].on == date(2026, 8, 11)


def test_reads_pre_market_timing():
    found = parse_earnings(
        _payload([{"symbol": "AMD", "time": "time-pre-market"}]),
        date(2026, 8, 10), WANTED,
    )
    assert found["AMD"].when == "pre-mkt"


def test_unknown_timing_is_blank_not_guessed():
    found = parse_earnings(_payload([{"symbol": "AMD", "time": ""}]),
                           date(2026, 8, 10), WANTED)
    assert found["AMD"].when == ""


def test_companies_we_do_not_hold_are_ignored():
    found = parse_earnings(
        _payload([{"symbol": "ZZZZ", "time": "time-pre-market"}]),
        date(2026, 8, 10), WANTED,
    )
    assert found == {}


def test_an_empty_page_is_not_an_error():
    assert parse_earnings({}, date(2026, 8, 10), WANTED) == {}
    assert parse_earnings(_payload([]), date(2026, 8, 10), WANTED) == {}


def test_the_earliest_report_date_wins():
    """A company listed twice must be flagged for the sooner date, since that
    is the one inside the holding period."""
    first = parse_earnings(_payload([{"symbol": "AMD", "time": ""}]),
                           date(2026, 8, 10), WANTED)
    later = parse_earnings(_payload([{"symbol": "AMD", "time": ""}]),
                           date(2026, 8, 14), WANTED)
    merged = dict(first)
    for symbol, event in later.items():
        merged.setdefault(symbol, event)
    assert merged["AMD"].on == date(2026, 8, 10)


def test_weekends_are_not_queried():
    """Saturday 2026-08-08 and Sunday 08-09 hold no calendar."""
    days = _session_days(date(2026, 8, 6), 7)
    assert all(d.weekday() < 5 for d in days)
    assert date(2026, 8, 8) not in days


def test_horizon_covers_the_holding_period():
    """The time limit is 60 bars of 30m — about 4.6 sessions — so a week of
    lookahead spans every day a position could still be open."""
    assert len(_session_days(date(2026, 8, 6), 7)) >= 5


def test_market_moving_releases_are_recognised():
    assert is_market_moving("FOMC Statement")
    assert is_market_moving("CPI m/m")
    assert is_market_moving("Nonfarm Payrolls")
    assert not is_market_moving("Redbook Retail Index")


def test_label_reads_as_a_human_would_write_it():
    event = Earnings(symbol="LITE", on=date(2026, 8, 11), when="after-close")
    assert "08/11" in event.label and "after-close" in event.label


def test_economic_parse_keeps_us_market_movers_once():
    from scanner.sources.calendar import parse_economic
    payload = {"data": {"rows": [
        {"country": "United States", "eventName": "CPI"},
        {"country": "United States", "eventName": "CPI"},
        {"country": "Germany", "eventName": "German CPI"},
        {"country": "China", "eventName": "CPI"},
        {"country": "United States", "eventName": "Redbook"},
    ]}}
    assert parse_economic(payload) == ("CPI",)


def test_economic_calendar_is_asked_for_the_next_day():
    # Nasdaq answers date=D with D-1's releases (checked 2026-10-08).
    from datetime import date
    from scanner.sources.calendar import economic_query_date
    assert economic_query_date(date(2026, 10, 14)) == date(2026, 10, 15)


def test_economic_parse_drops_sub_series_of_the_same_release():
    from scanner.sources.calendar import parse_economic
    names = ["Core CPI", "Core CPI Index", "CPI", "CPI Index, n.s.a.", "CPI Index, s.a",
             "CPI, n.s.a", "Cleveland CPI", "Philly Fed Prices Paid",
             "Philadelphia Fed Manufacturing Index", "Atlanta Fed GDPNow", "Fed Waller Speaks"]
    payload = {"data": {"rows": [{"country": "United States", "eventName": n} for n in names]}}
    assert parse_economic(payload) == ("Core CPI", "CPI", "Philadelphia Fed Manufacturing Index",
                                       "Fed Waller Speaks")
