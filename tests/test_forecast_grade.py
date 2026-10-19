"""Grading Gemma's 5-day forecasts (WORKLIST §65): does a stock she rates above
0.5 beat the S&P 500 more often than one she rates below?"""

import json
from datetime import date, timedelta

from scanner.advisor import forecast_grade as fg
from scanner.features.bars import Bars


def _bars(symbol, start, closes, opens=None):
    days = []
    d = start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    opens = opens or closes
    return Bars(symbol=symbol, dates=tuple(days), opens=tuple(opens), closes=tuple(closes),
                highs=tuple(closes), lows=tuple(closes), volumes=tuple(1.0 for _ in closes))


def _decision(day, forecasts, submitted=True):
    return {"event": "gemma_decision", "at": f"{day}T05:46:00-08:00",
            "reason": json.dumps({"forecasts": forecasts, "submitted": submitted})}


def test_extract_takes_submitted_decisions_last_per_day():
    rows = [_decision("2026-10-19", {"MU": 0.6}),
            _decision("2026-10-19", {"MU": 0.7}),
            _decision("2026-10-20", {"VLO": 0.4}, submitted=False),
            {"event": "gemma_decision", "at": "2026-10-16T05:46", "reason": "{}"}]
    got = fg.extract(rows)
    assert [(f.day, f.symbol, f.prob) for f in got] == [("2026-10-19", "MU", 0.7)]


def test_outcome_is_5_sessions_from_the_open_against_the_index():
    start = date(2026, 10, 19)
    stock = _bars("MU", start, [110, 111, 112, 113, 120, 121], opens=[100] * 6)
    spx = _bars("^GSPC", start, [101, 101, 101, 101, 105, 106], opens=[100] * 6)
    # open 100 -> close of the 5th session 120 (+20%) vs 100 -> 105 (+5%)
    assert round(fg.excess(stock, spx, "2026-10-19"), 6) == 15.0
    assert fg.excess(stock, spx, "2026-10-21") is None   # not 5 sessions yet


def test_score_compares_her_brier_with_no_view():
    graded = [fg.Graded(day="d", symbol=s, prob=p, excess=e) for s, p, e in
              (("A", 0.8, 3.0), ("B", 0.7, 1.0), ("C", 0.3, -2.0), ("D", 0.2, -1.0))]
    sc = fg.score(graded)
    assert sc["n"] == 4 and sc["hit_rate"] == 1.0
    assert round(sc["brier"], 4) == round((0.04 + 0.09 + 0.09 + 0.04) / 4, 4)
    assert sc["brier_no_view"] == 0.25
    assert sc["skill"] > 0


def test_too_few_forecasts_say_so():
    assert "not enough" in fg.text(fg.score([]))
    sc = fg.score([fg.Graded(day="d", symbol="A", prob=0.6, excess=1.0)])
    assert "not enough" in fg.text(sc)


def test_text_reports_the_score_plainly():
    graded = [fg.Graded(day="d", symbol=str(i), prob=0.6, excess=1.0 if i % 2 else -1.0)
              for i in range(40)]
    text = fg.text(fg.score(graded))
    assert "40 graded" in text and "0.25" in text
