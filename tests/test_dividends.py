"""Ex-dividend handling — parsing, validation, and the stop-erosion warning."""

from __future__ import annotations

from datetime import date

from scanner.sources.dividends import (
    DividendEvent,
    by_symbol,
    next_ex_date,
    parse,
)


def _raw(symbol="MO", cash="1.06", ex="2026-09-15", pay="2026-10-10"):
    return {"initiating_symbol": symbol, "cash": cash, "ex_date": ex,
            "payable_date": pay, "record_date": ex, "ca_sub_type": "cash",
            "old_rate": "1", "new_rate": "1"}


def test_parses_a_clean_record():
    events = parse([_raw()])
    assert len(events) == 1
    assert events[0].symbol == "MO"
    assert events[0].cash == 1.06
    assert events[0].ex_date == date(2026, 9, 15)
    assert events[0].payable_date == date(2026, 10, 10)


def test_zero_cash_records_are_dropped_not_defaulted():
    """81 of 650 live records carried cash of zero.

    A zero-cash event reports a 0% drop, which would wave through exactly the
    position this module exists to flag. Silence is safer than a false all-clear.
    """
    assert parse([_raw(cash="0")]) == ()
    assert parse([_raw(cash="0.00")]) == ()
    assert parse([_raw(cash=None)]) == ()


def test_records_without_an_ex_date_are_dropped():
    assert parse([_raw(ex=None)]) == ()
    assert parse([_raw(ex="")]) == ()
    assert parse([_raw(ex="not-a-date")]) == ()


def test_past_ex_dates_are_filtered_out():
    """The API filters on PAYABLE date, so a window is full of events whose
    ex-date has already passed — 511 of 650 in a live pull. Reading `ex_date`
    off a date-filtered result without re-checking it is a mistake that looks
    like it works."""
    records = [_raw(ex="2026-07-15"), _raw(symbol="KO", ex="2026-09-20")]
    events = parse(records, on_or_after=date(2026, 8, 7))
    assert [e.symbol for e in events] == ["KO"]


def test_malformed_payloads_never_raise():
    assert parse(None) == ()
    assert parse({"error": "nope"}) == ()
    assert parse(["a string", 42, None]) == ()
    assert parse([{}]) == ()


def test_events_sort_by_ex_date():
    events = parse([_raw(symbol="B", ex="2026-09-20"),
                    _raw(symbol="A", ex="2026-09-10")])
    assert [e.symbol for e in events] == ["A", "B"]


# --- the part that protects the swing strategy ---------------------------

def test_drop_pct_is_the_dividend_over_the_price():
    event = DividendEvent(symbol="MO", cash=1.06, ex_date=date(2026, 9, 15))
    assert round(event.drop_pct(67.75), 2) == 1.56


def test_a_dividend_can_exceed_the_whole_stop():
    """MO drops 1.56% on the ex-date against a 1.5% minimum stop.

    A long held through it is stopped out by an accounting adjustment. This is
    the case the backtest could not see: Yahoo's adjusted bars smooth the drop
    away retroactively, live bars do not.
    """
    event = DividendEvent(symbol="MO", cash=1.06, ex_date=date(2026, 9, 15))
    assert event.drop_pct(67.75) > 1.5
    assert event.erodes_stop(67.75, stop_pct=1.5) is True


def test_a_small_dividend_against_a_wide_stop_is_not_flagged():
    event = DividendEvent(symbol="O", cash=0.271, ex_date=date(2026, 9, 15))
    assert event.erodes_stop(62.36, stop_pct=5.0) is False


def test_erosion_threshold_is_a_ratio_not_an_absolute():
    event = DividendEvent(symbol="KO", cash=0.53, ex_date=date(2026, 9, 15))
    assert event.erodes_stop(86.85, stop_pct=1.5) is True    # 0.61/1.5 = 41%
    assert event.erodes_stop(86.85, stop_pct=3.0) is False   # 0.61/3.0 = 20%


def test_zero_price_or_stop_is_safe():
    event = DividendEvent(symbol="X", cash=1.0, ex_date=date(2026, 9, 15))
    assert event.drop_pct(0.0) == 0.0
    assert event.erodes_stop(50.0, stop_pct=0.0) is False


# --- lookups --------------------------------------------------------------

def test_next_ex_date_picks_the_soonest():
    events = parse([_raw(symbol="MO", ex="2026-11-15"),
                    _raw(symbol="MO", ex="2026-09-15")])
    assert next_ex_date(events, "MO").ex_date == date(2026, 9, 15)
    assert next_ex_date(events, "mo").ex_date == date(2026, 9, 15)
    assert next_ex_date(events, "KO") is None


def test_by_symbol_keeps_only_the_soonest_per_name():
    events = parse([_raw(symbol="MO", ex="2026-11-15"),
                    _raw(symbol="MO", ex="2026-09-15"),
                    _raw(symbol="KO", ex="2026-10-01")])
    lookup = by_symbol(events)
    assert set(lookup) == {"MO", "KO"}
    assert lookup["MO"].ex_date == date(2026, 9, 15)
