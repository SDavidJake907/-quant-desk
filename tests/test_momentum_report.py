"""The monthly momentum report: what each pick is, and what TradingAgents said.

Record-only. These tests pin that the report shows every pick, marks ones
TradingAgents could not rate, carries the concentration warning, and says in
so many words that ratings do not change the book.
"""

from __future__ import annotations

from scanner.momentum import report


RECORD = {
    "date": "2026-10-06", "tranche": 0, "invested": True, "sp500": 7722.7, "sma210": 7208.0,
    "picks": ["SNDK", "MRNA", "MU"],
    "momentum": {"SNDK": 1152.6, "MRNA": 424.7, "MU": 422.3},
    "sectors": {"Information Technology": 2, "Health Care": 1},
    "prices": {"SNDK": 1719.99, "MRNA": 190.01, "MU": 1074.89},
}
SECTOR = {"SNDK": "Information Technology", "MRNA": "Health Care", "MU": "Information Technology"}


def test_excerpt_takes_the_executive_summary_and_trims():
    text = "**Rating**: Hold\n\n**Executive Summary**:\nStay flat. Wait for the breakout.\n\n**Investment Thesis**: long"
    assert report.excerpt(text, limit=200).startswith("Stay flat. Wait for the breakout.")
    assert len(report.excerpt("x" * 1000, limit=100)) <= 101


def test_render_lists_every_pick_and_marks_unrated():
    ratings = {"SNDK": {"rating": "Overweight", "trader": "Buy", "summary": "Strong trend."},
               "MRNA": {"rating": "Underweight", "trader": "Sell", "summary": "Trial risk."}}
    md = report.render(RECORD, ratings, SECTOR)
    for sym in ("SNDK", "MRNA", "MU"):
        assert sym in md
    assert "not rated" in md
    assert "Underweight" in md and "Trial risk." in md


def test_render_says_ratings_do_not_change_the_book():
    md = report.render(RECORD, {}, SECTOR)
    assert "record-only" in md.lower()


def test_render_carries_the_concentration_warning():
    concentrated = {**RECORD, "sectors": {"Information Technology": 8, "Health Care": 2}}
    md = report.render(concentrated, {}, SECTOR)
    assert "8 of" in md


def test_rating_rows_hold_what_the_later_test_needs():
    ratings = {"SNDK": {"rating": "Overweight", "trader": "Buy", "summary": ""}}
    rows = report.rating_rows(RECORD, ratings)
    assert rows == [{"date": "2026-10-06", "symbol": "SNDK", "rank": 1, "momentum": 1152.6,
                     "rating": "Overweight", "trader": "Buy", "price": 1719.99, "tranche": 0}]
