from datetime import datetime, timedelta, timezone

from scanner.advisor import after_hours as ah

NOW = datetime(2026, 10, 22, 21, 30, tzinfo=timezone.utc)          # 17:30 ET


def test_stock_line_shows_the_move_from_the_close():
    q = ah.Quote(last=90.02, ref=79.42, at=NOW - timedelta(minutes=9))
    line = ah.stock_line(q, NOW)
    assert "+13.3%" in line and "90.02" in line and "79.42" in line and "17:21 ET" in line


def test_stock_line_says_when_nothing_traded():
    assert ah.stock_line(None, NOW) == "after-hours: no trade since the close"


def test_stock_line_flags_a_stale_trade():
    q = ah.Quote(last=854.0, ref=856.49, at=NOW - timedelta(hours=3))
    assert "no trade in the last hour" in ah.stock_line(q, NOW)


def test_futures_text_lists_each_index():
    quotes = {"S&P 500": ah.Quote(7703.75, 7800.25, NOW - timedelta(minutes=5)),
              "Nasdaq 100": ah.Quote(29094.75, 30007.0, NOW - timedelta(minutes=5))}
    text = ah.futures_text(quotes, NOW)
    assert "S&P 500 futures 7,703.75 (-1.24%" in text
    assert "Nasdaq 100 futures 29,094.75 (-3.04%" in text


def test_futures_text_empty_when_unavailable():
    assert ah.futures_text({"S&P 500": None}, NOW) == ""
