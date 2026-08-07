"""Index quote tests.

The previous-close derivation is the highest-risk code in the repo: a wrong
baseline silently produces a wrong percentage, and a wrong percentage looks
exactly like a right one. These tests pin the behaviour that motivated it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scanner.sources.indices import QuoteError, parse_chart

# 2026-08-05 08:00 JST and 2026-08-06 08:45 JST
AUG5_JST = 1785970800
AUG6_JST = 1786060800


def _payload(timestamps, closes, price, market_time, *, tz="Asia/Tokyo", period=None):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "exchangeTimezoneName": tz,
                        "regularMarketTime": market_time,
                        "currency": "JPY",
                        "shortName": "Nikkei 225",
                        "fiftyTwoWeekHigh": 72831.73,
                        "currentTradingPeriod": {"regular": period} if period else {},
                    },
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


def test_previous_close_ignores_current_session():
    """The current session's own bar must never be its own baseline."""
    quote = parse_chart(
        _payload([AUG5_JST, AUG6_JST], [63957.53, 66300.44], 66300.44, AUG6_JST),
        "^N225", "Nikkei 225", "Asia-Pacific", "Japan",
    )
    assert quote.previous_close == pytest.approx(63957.53)
    assert quote.change == pytest.approx(2342.91)
    assert quote.change_pct == pytest.approx(3.663, abs=0.01)


def test_skips_null_closes_from_holidays():
    quote = parse_chart(
        _payload([AUG5_JST - 86400, AUG5_JST, AUG6_JST], [61000.0, None, 66300.44], 66300.44, AUG6_JST),
        "^N225", "Nikkei 225", "Asia-Pacific", "Japan",
    )
    assert quote.previous_close == pytest.approx(61000.0)


def test_raises_rather_than_guessing_a_baseline():
    """No prior session means no percentage. Refuse instead of inventing one."""
    with pytest.raises(QuoteError, match="previous close"):
        parse_chart(
            _payload([AUG6_JST], [66300.44], 66300.44, AUG6_JST),
            "^N225", "Nikkei 225", "Asia-Pacific", "Japan",
        )


def test_negative_move_reported_correctly():
    quote = parse_chart(
        _payload([AUG5_JST, AUG6_JST], [10908.4, 10888.3], 10888.3, AUG6_JST, tz="Europe/London"),
        "^FTSE", "FTSE 100", "Europe", "United Kingdom",
    )
    assert quote.change < 0
    assert quote.change_pct == pytest.approx(-0.184, abs=0.01)


def test_market_open_flag_from_trading_period():
    now = int(datetime.now(timezone.utc).timestamp())
    open_quote = parse_chart(
        _payload([AUG5_JST, AUG6_JST], [1.0, 2.0], 2.0, AUG6_JST,
                 period={"start": now - 3600, "end": now + 3600}),
        "^N225", "Nikkei 225", "Asia-Pacific", "Japan",
    )
    assert open_quote.is_open

    closed_quote = parse_chart(
        _payload([AUG5_JST, AUG6_JST], [1.0, 2.0], 2.0, AUG6_JST,
                 period={"start": now - 7200, "end": now - 3600}),
        "^N225", "Nikkei 225", "Asia-Pacific", "Japan",
    )
    assert not closed_quote.is_open


def test_quote_time_converts_to_exchange_timezone():
    quote = parse_chart(
        _payload([AUG5_JST, AUG6_JST], [1.0, 2.0], 2.0, AUG6_JST),
        "^N225", "Nikkei 225", "Asia-Pacific", "Japan",
    )
    assert quote.local_time.tzinfo is not None
    assert quote.quote_time.tzinfo == timezone.utc


def test_pct_off_52w_high_is_negative_below_the_high():
    quote = parse_chart(
        _payload([AUG5_JST, AUG6_JST], [63957.53, 66300.44], 66300.44, AUG6_JST),
        "^N225", "Nikkei 225", "Asia-Pacific", "Japan",
    )
    assert quote.pct_off_52w_high is not None
    assert quote.pct_off_52w_high < 0


def test_empty_result_raises():
    with pytest.raises(QuoteError):
        parse_chart({"chart": {"result": []}}, "^X", "X", "Europe", "UK")
