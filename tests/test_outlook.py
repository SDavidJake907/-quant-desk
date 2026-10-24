"""Gemma's 10-day outlooks, where and how far (advisor/outlook.py, operator + Gemma 2026-10-25)."""

import json
from datetime import date, timedelta

from scanner.advisor import outlook as ol
from scanner.features.bars import Bars

DAY = date(2026, 10, 26)


def bars(closes, start=DAY):
    days = [start + timedelta(days=i) for i in range(len(closes))]
    return Bars(symbol="X", dates=tuple(days), opens=tuple(closes), highs=tuple(closes),
                lows=tuple(closes), closes=tuple(closes), volumes=tuple(1e6 for _ in closes))


def answer(*items):
    return {"outlooks": [dict(zip(("symbol", "p_up", "low_pct", "high_pct", "lean", "basis"), i)) for i in items]}


def test_parse_takes_buys_and_weekly_holdings_only():
    a = answer(("MU", 65, -4, 8, "news", "b"), ("KO", 55, -2, 3, "indicators", "b"), ("ZZ", 50, -1, 1, "news", ""))
    got = ol.parse(a, day=DAY, buys=["MU"], kept=["MU", "KO"], holdings_due=False, prices={"MU": 100, "KO": 60})
    assert [o.symbol for o in got] == ["MU"] and got[0].kind == "buy" and got[0].p_up == 0.65
    got = ol.parse(a, day=DAY, buys=["MU"], kept=["MU", "KO"], holdings_due=True, prices={"MU": 100, "KO": 60})
    assert {o.symbol: o.kind for o in got} == {"MU": "buy", "KO": "holding"}


def test_parse_clamps_and_orders_the_range_and_needs_a_price():
    got = ol.parse(answer(("MU", 100, 9, -3, "hunch", "")), day=DAY, buys=["MU"], kept=[], holdings_due=False,
                   prices={"MU": 100})
    assert got[0].p_up == ol.P_CEIL and (got[0].low_pct, got[0].high_pct) == (-3, 9) and got[0].lean == "balanced"
    assert ol.parse(answer(("MU", 60, -3, 9, "news", "")), day=DAY, buys=["MU"], kept=[], holdings_due=False,
                    prices={}) == []


def test_graded_on_the_tenth_session_after_the_day():
    o = ol.Outlook(day=DAY.isoformat(), symbol="X", kind="buy", price=100.0, p_up=0.7, low_pct=-5, high_pct=5,
                   lean="news")
    assert ol.realized(o, bars([100] * 10)) is None                   # day itself + 9 after: not yet
    b = bars([100] * 10 + [104])
    assert round(ol.realized(o, b), 6) == 4.0
    g = ol.grade([o], {"X": b})[0]
    assert g.up and g.inside


def test_score_and_text_show_from_twenty():
    def g(p, r, lean):
        o = ol.Outlook(day="2026-10-26", symbol="X", kind="buy", price=1, p_up=p, low_pct=-5, high_pct=5, lean=lean)
        return ol.Graded(outlook=o, ret_pct=r)
    gs = [g(0.8, 3, "news")] * 15 + [g(0.6, -8, "indicators")] * 5
    sc = ol.score(gs)
    assert sc["n"] == 20 and sc["inside"] == 0.75 and set(sc["by_lean"]) == {"news", "indicators"}
    assert sc["hit"] == 0.75 and "6 of 8" in ol.text(sc)
    assert "Brier" in ol.text(sc) and "leaning on news" in ol.text(sc)
    assert "shown from 20" in ol.text(ol.score(gs[:5]))


def test_extract_and_weekly_due():
    o = ol.Outlook(day="2026-10-26", symbol="KO", kind="holding", price=60, p_up=0.5, low_pct=-2, high_pct=2,
                   lean="balanced")
    rows = [{"event": "gemma_decision", "reason": json.dumps({"submitted": True, "outlooks": [o.model_dump()]})},
            {"event": "gemma_decision", "reason": json.dumps({"submitted": False, "outlooks": [o.model_dump()]})}]
    made = ol.extract(rows)
    assert made == [o]
    assert not ol.holdings_due(made, date(2026, 10, 28)) and ol.holdings_due(made, date(2026, 11, 2))


def test_schema_adds_outlooks():
    s = ol.with_schema({"type": "object", "properties": {"journal": {}}, "required": ["journal"]}, ["MU"])
    assert "outlooks" in s["required"] and s["properties"]["outlooks"]["items"]["properties"]["symbol"]["enum"] == ["MU"]
    assert "every holding" in ol.rules(True) and "EVERY new buy (none" in ol.rules(False)
