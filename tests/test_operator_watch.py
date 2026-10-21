from scanner.advisor import operator_watch as ow
from scanner.advisor.gemma_review import PositionView


def _view(symbol="AAPL", *, price=100.0, stop=95.0):
    return PositionView(symbol=symbol, qty=10, entry=98.0, price=price, value=price * 10,
                        pl_dollars=(price - 98.0) * 10, pl_pct=(price / 98.0 - 1) * 100,
                        weight_pct=10.0, stop=stop,
                        stop_gap_pct=None if stop is None else (price - stop) / price * 100)


def test_code_flags_earnings_near_stop_and_missing_stop():
    assert ow.code_flags(_view(stop=80.0), atr_pct=2.0, earnings=None) == []
    near = ow.code_flags(_view(stop=98.5), atr_pct=2.0, earnings=None)
    assert len(near) == 1 and "within 1 daily ATR" in near[0]
    both = ow.code_flags(_view(stop=80.0), atr_pct=2.0, earnings="Wed 10/28 after-close")
    assert "earnings Wed 10/28" in both[0]
    assert ow.code_flags(_view(stop=None), atr_pct=2.0, earnings=None) == ["no live stop found"]


def test_validate_raises_flagged_ok_to_watch_and_fills_skipped():
    views = [_view("AAPL"), _view("MET"), _view("AMZN")]
    flags = {"AAPL": ["earnings Thu 10/29"], "AMZN": ["no live stop found"]}
    answer = {"positions": [
        {"symbol": "AAPL", "verdict": "ok", "reason": "fine", "watch_for": "x"},
        {"symbol": "MET", "verdict": "concern", "reason": "downgrade", "watch_for": "y"},
        {"symbol": "NOPE", "verdict": "concern", "reason": "?", "watch_for": ""}]}
    rows = ow.validate(answer, views, flags)
    assert [r["verdict"] for r in rows] == ["watch", "concern", "watch"]
    assert rows[2]["reason"] == "(no answer from Gemma)"
    assert rows[0]["flags"] == ["earnings Thu 10/29"]
    assert ow.concerns(rows) == ["MET: downgrade"]


def test_validate_survives_junk():
    rows = ow.validate("not json", [_view("AAPL")], {})
    assert rows[0]["verdict"] == "ok"


def test_prompt_never_mentions_the_competition():
    text = ow.prompt({"AAPL": "AAPL block"}, [_view("AAPL")], {"AAPL": ["earnings x"]},
                     market="S&P up").lower()
    for word in ("beat", "competition", "scoreboard", "your book", "ghost"):
        assert word not in text
    assert "flags: earnings x" in text and "aapl block" in text


def test_grade_groups_by_verdict():
    g = ow.grade([("ok", 1.0), ("ok", 3.0), ("concern", -2.0)])
    assert g == {"ok": {"n": 2, "mean_excess_pct": 2.0},
                 "concern": {"n": 1, "mean_excess_pct": -2.0}}


def test_schema_limits_symbols():
    s = ow.schema(["AAPL", "MET"])
    item = s["properties"]["positions"]["items"]["properties"]
    assert item["symbol"]["enum"] == ["AAPL", "MET"]
    assert item["verdict"]["enum"] == list(ow.VERDICTS)
