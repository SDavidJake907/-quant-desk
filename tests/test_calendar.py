"""Event-calendar tests.

This is the one addition to the strategy that removes a known risk rather than
adding an unvalidated signal. The property that matters most is that a failure
to read the calendar is never mistaken for a clean calendar — silently trading
through earnings because an API was down is worse than not checking at all.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import httpx
import pytest

from scanner.sources.calendar import (
    EARNINGS_DATE_OFFSET,
    Earnings,
    ECONOMIC_DATE_OFFSET,
    _session_days,
    economic_query_date,
    is_market_moving,
    parse_earnings,
    upcoming_earnings,
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


def test_regional_fed_surveys_and_pce_sub_series_are_dropped():
    from scanner.sources.calendar import parse_economic
    names = ["KC Fed Manufacturing Index", "Dallas Fed PCE", "Chicago Fed National Activity",
             "PCE Price index", "PCE price index", "Core PCE Prices", "GDP", "GDP Price Index"]
    payload = {"data": {"rows": [{"country": "United States", "eventName": n} for n in names]}}
    assert parse_economic(payload) == ("PCE Price index", "GDP")


# ---- upcoming_earnings: offset and failure tracking ----

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    """Captures query dates and returns canned earnings payloads."""

    def __init__(self, payloads_by_date: dict[date, dict], fail_dates: set[date] = None):
        self.payloads_by_date = payloads_by_date
        self.fail_dates = fail_dates or set()
        self.queried_dates: list[date] = []

    async def get(self, url, *, params=None, headers=None, timeout=None):
        q = params.get("date") if params else None
        qd = date.fromisoformat(q) if q else None
        self.queried_dates.append(qd)
        if qd in self.fail_dates:
            raise httpx.HTTPError("boom")
        return _FakeResponse(self.payloads_by_date.get(qd, {"data": {"rows": []}}))


def _earnings_payload(symbols: list[tuple[str, str]]) -> dict:
    """symbols: [(symbol, time), ...]"""
    return {"data": {"rows": [{"symbol": s, "time": t} for s, t in symbols]}}


async def test_upcoming_earnings_asks_for_the_day_itself():
    """date=D returns D's reports (checked 2026-10-22: date=10-22 listed RNG, which reported
    10-22 after the close; date=10-21 listed TSLA/IBM/T). Today's reports must be seen."""
    target = date(2026, 10, 22)
    client = _FakeClient({target: _earnings_payload([("RNG", "after-hours")])})
    found, failed = await upcoming_earnings(frozenset({"RNG"}), client, horizon=1, today=target)
    assert failed == ()
    assert target in client.queried_dates
    assert found["RNG"].on == target


async def test_upcoming_earnings_applies_offset():
    """Stores D's earnings under D whatever the query offset is."""
    target = date(2026, 10, 15)
    client = _FakeClient({target + EARNINGS_DATE_OFFSET: _earnings_payload([("NVDA", "after-hours")])})
    found, failed = await upcoming_earnings(frozenset({"NVDA"}), client, horizon=1, today=target)
    assert failed == ()
    assert target + EARNINGS_DATE_OFFSET in client.queried_dates
    assert "NVDA" in found
    assert found["NVDA"].on == target  # stored with correct date, not query date
    assert found["NVDA"].when == "after-close"  # normalized by parse_earnings


async def test_upcoming_earnings_tracks_failed_days():
    """Days that fail to load are reported separately from days with no earnings."""
    target = date(2026, 10, 15)
    client = _FakeClient({}, fail_dates={target + EARNINGS_DATE_OFFSET})
    found, failed = await upcoming_earnings(frozenset({"NVDA"}), client, horizon=1, today=target)
    assert found == {}
    assert failed == (target,)


async def test_upcoming_earnings_earliest_date_wins():
    """If a symbol reports on two days, the earliest is kept."""
    d1 = date(2026, 10, 15)
    d2 = date(2026, 10, 16)
    client = _FakeClient({
        d1 + EARNINGS_DATE_OFFSET: _earnings_payload([("NVDA", "after-hours")]),
        d2 + EARNINGS_DATE_OFFSET: _earnings_payload([("NVDA", "pre-mkt")]),
    })
    found, failed = await upcoming_earnings(frozenset({"NVDA"}), client, horizon=2, today=d1)
    assert failed == ()
    assert found["NVDA"].on == d1  # earliest wins
    assert found["NVDA"].when == "after-close"  # normalized by parse_earnings


async def test_upcoming_earnings_filters_to_wanted_symbols():
    """Symbols not in `wanted` are ignored."""
    target = date(2026, 10, 15)
    client = _FakeClient({
        target + EARNINGS_DATE_OFFSET: _earnings_payload([("NVDA", "after-hours"), ("ZZZZ", "pre-mkt")]),
    })
    found, failed = await upcoming_earnings(frozenset({"NVDA"}), client, horizon=1, today=target)
    assert "NVDA" in found
    assert "ZZZZ" not in found


def test_earnings_offset_is_zero():
    """The earnings calendar is NOT shifted like the economic one (fixed 2026-10-22)."""
    assert EARNINGS_DATE_OFFSET == timedelta(0)
    assert ECONOMIC_DATE_OFFSET == timedelta(days=1)


async def test_upcoming_earnings_with_horizon_queries_each_session_day():
    """With horizon=2, queries both session days (skipping weekends)."""
    d1 = date(2026, 10, 15)  # Thursday
    d2 = date(2026, 10, 16)  # Friday
    client = _FakeClient({
        d1 + EARNINGS_DATE_OFFSET: _earnings_payload([]),
        d2 + EARNINGS_DATE_OFFSET: _earnings_payload([]),
    })
    found, failed = await upcoming_earnings(frozenset(), client, horizon=2, today=d1)
    assert failed == ()
    assert d1 + EARNINGS_DATE_OFFSET in client.queried_dates
    assert d2 + EARNINGS_DATE_OFFSET in client.queried_dates
