"""Desk officer checks: missed runs, error lines, cross-book concentration."""

from __future__ import annotations

from datetime import date

from scanner.ops import deskcheck as dc

DAY = date(2026, 10, 7)
SCAN = "".join(f"===== Wed 10/07/2026 {h:02d}:{m:02d}:02.00 =====\nok\n"
               for h, m in [(5, 33), (6, 3)])
FLATTEN = ("===== FLATTEN Wed 10/07/2026 11:45:00.10 =====\nnot the closing run\n"
           "===== FLATTEN Tue 10/06/2026 11:45:00.10 =====\nTraceback (most recent call last)\n")
MOMENTUM = ("===== MOMENTUM Wed 10/07/2026 11:40:00.10 =====\n"
            "     FAILED buy 3 MU: insufficient buying power\n")


def test_runs_are_counted_per_day():
    counts = dc.runs_on(DAY, {"scan": SCAN, "flatten": FLATTEN})
    assert counts == {"scan": 2, "flatten": 1}


def test_missing_runs_are_problems():
    found = dc.missing_runs({"scan": 14, "flatten": 1}, {"scan": 14, "flatten": 4, "data": 1})
    texts = [f.text for f in found]
    assert texts == ["flatten ran 1 of 4 expected times today", "data ran 0 of 1 expected times today"]


def test_trouble_lines_only_from_todays_runs():
    found = dc.trouble_lines(DAY, {"flatten": FLATTEN, "momentum": MOMENTUM})
    assert [f.text.split(":")[0] for f in found] == ["momentum"]   # yesterday's traceback ignored


def test_cross_book_concentration_is_flagged():
    values = {"AMD": 5000, "MRVL": 4000, "MU": 3000, "GS": 2000, "TSM": 6000}
    sectors = {"AMD": "IT", "MRVL": "IT", "MU": "IT", "GS": "Financials"}
    shares, flags = dc.exposure(values, sectors)
    assert round(shares["IT"]) == 60
    assert "unknown (non-S&P listing)" in shares
    assert len(flags) == 1 and flags[0].text.startswith("IT is 60%")
    assert dc.exposure({}, {}) == ({}, [])


def test_books_total_by_owner():
    rows = dc.books([{"symbol": "AMD", "market_value": "100", "unrealized_pl": "5"},
                     {"symbol": "MU", "market_value": "50", "unrealized_pl": "-2"}],
                    {"AMD": "picks", "MU": "momentum"})
    assert rows["picks"] == {"positions": 1, "value": 100.0, "unrealized": 5.0}


def test_routine_lowercase_counts_are_not_alarms():
    text = ("===== OUTCOMES Wed 10/07/2026 13:30:01.00 =====\n"
            "tradingagents  117 rating(s), 0 failed run(s)\n"
            "news gate  2 model refusal(s), failed open on 18 scan(s)\n"
            "NEWS GATE FAILING OPEN -- Ollama unreachable\n")
    found = dc.trouble_lines(DAY, {"outcomes": text})
    assert [f.text for f in found] == ["outcomes: NEWS GATE FAILING OPEN -- Ollama unreachable"]
