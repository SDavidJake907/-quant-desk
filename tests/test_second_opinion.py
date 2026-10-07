"""Second opinions: tiers are fixed, calls parse, grading follows its pre-set rule."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scanner.advisor import grading, opinions as op


def ta(symbol, at, rating):
    return {"event": "tradingagents_rating", "symbol": symbol, "at": at,
            "reason": f"{rating}; trader=x; mode=premarket"}


def test_only_momentum_is_primary_and_noise_is_never_graded():
    tiers = {o.key: o.tier for o in op.OPINIONS}
    assert [k for k, t in tiers.items() if t == op.PRIMARY] == ["momentum"]
    graded = {key for key, _ in op.SOURCES.values()}
    assert graded == {k for k, t in tiers.items() if t == op.UNPROVEN}
    assert not graded & {k for k, t in tiers.items() if t == op.NOISE}


@pytest.mark.parametrize("reason,want", [
    ("Overweight; trader=Buy", 1), ("Buy; x", 1), ("Hold; trader=Hold", 0),
    ("Underweight; trader=Sell", -1), ("Sell", -1), ("garbled", None)])
def test_ta_direction(reason, want):
    assert op.ta_direction(reason) == want


@pytest.mark.parametrize("reason,want", [
    ("hold (strong, grounded): v2", 1), ("exit (weak): x", -1), ("", None)])
def test_gemma_direction(reason, want):
    assert op.gemma_direction(reason) == want


def test_calls_keep_last_per_day_and_drop_holds():
    rows = [ta("AMD", "2026-10-05T04:00", "Sell"),
            ta("AMD", "2026-10-05T09:00", "Overweight"),   # same day: last wins
            ta("MU", "2026-10-05T04:00", "Hold"),          # hold: not graded
            {"event": "trade_intent", "symbol": "AMD", "at": "2026-10-05"}]
    calls = grading.calls_from_rows(rows)
    assert [(c.symbol, c.direction) for c in calls] == [("AMD", 1)]


def series(start, closes):
    return [(start + timedelta(days=i), c) for i, c in enumerate(closes)]


def test_forward_return_waits_for_the_horizon():
    s = series(date(2026, 1, 1), [100, 101, 102, 110])
    assert grading.forward_return(s, date(2026, 1, 1), 3) == pytest.approx(0.10)
    assert grading.forward_return(s, date(2026, 1, 2), 3) is None      # pending
    assert grading.forward_return(s, date(2025, 12, 1), 1) is None     # before data


def test_grade_signs_by_direction_and_isolates_disagreements():
    d0 = date(2026, 1, 1)
    bench = series(d0, [100, 100, 100])                      # flat market
    prices = {"UP": series(d0, [100, 105, 110]), "DN": series(d0, [100, 95, 90])}
    calls = [grading.Call(opinion="tradingagents", symbol="UP", day=d0, direction=-1),
             grading.Call(opinion="tradingagents", symbol="DN", day=d0, direction=-1),
             grading.Call(opinion="tradingagents", symbol="UP", day=d0 + timedelta(days=1),
                          direction=1)]
    rec = grading.grade(calls, prices, bench, picks_on={d0: frozenset({"UP"})},
                        horizon=2)["tradingagents"]
    assert rec.graded == 2 and rec.pending == 1
    assert rec.mean == pytest.approx(0.0)                    # -10 and +10
    assert rec.disagreements == 1                            # sold a momentum pick
    assert rec.disagreement_mean == pytest.approx(-10.0)     # and was wrong
    assert not rec.graduated


def test_snapshot_flags_a_sell_on_a_momentum_pick(tmp_path):
    rows = [ta("MU", "2026-10-06T04:00", "Underweight")]
    snap = op.snapshot("MU", rows, ("MU", "SNDK"))
    assert snap["momentum"] == "top-10 pick"
    assert snap["disagrees_with_primary"] == ["tradingagents"]
    assert op.snapshot("NKE", rows, ("MU",))["disagrees_with_primary"] == []


def test_momentum_picks_prefer_the_paper_record(tmp_path):
    paper, dry = tmp_path / "paper.jsonl", tmp_path / "dry.jsonl"
    dry.write_text(json.dumps({"date": "2026-10-06", "picks": ["A"], "invested": True}))
    assert op.momentum_picks((paper, dry)) == ("2026-10-06", ("A",), True)
    paper.write_text(json.dumps({"date": "2026-10-07", "picks": ["B"], "invested": True}))
    assert op.momentum_picks((paper, dry))[1] == ("B",)
    assert op.momentum_picks((tmp_path / "none.jsonl",)) == ("", (), None)
