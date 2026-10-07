"""Scorecard: each closed trade counted once, under the strategy that placed it."""

from __future__ import annotations

import json

import pytest

from scanner.execution import scorecard


def outcome(coid, r, dollars):
    return {"event": "trade_outcome",
            "outcome": {"client_order_id": coid, "realized_r": r,
                        "realized_dollars": dollars}}


def test_trades_are_grouped_by_prefix_and_counted_once():
    rows = [
        outcome("smc-CCL-1", -1.0, -400.0),
        outcome("smc-EXPE-2", 3.0, 1100.0),
        outcome("smc-EXPE-2", 3.0, 1100.0),          # re-recorded: count once
        outcome("man-NVDA-3", 2.0, 300.0),
        {"event": "trade_intent", "intent": {"client_order_id": "man-X"}},
        outcome("", 5.0, 5.0),                        # no id: unattributable
    ]
    stats = scorecard.by_strategy(rows)
    assert set(stats) == {"smc", "manual"}
    smc = stats["smc"]
    assert (smc.trades, smc.wins, smc.total_r, smc.total_dollars) == (2, 1, 2.0, 700.0)
    assert smc.mean_r == 1.0 and smc.win_rate == 0.5
    assert stats["manual"].trades == 1


def test_empty_stats_do_not_divide_by_zero():
    s = scorecard.Stats(strategy="manual", trades=0, wins=0, total_r=0, total_dollars=0)
    assert s.mean_r == 0.0 and s.win_rate == 0.0


def test_book_return():
    assert scorecard.book_return(50_000, 10_000, 45_000) == pytest.approx(10.0)
    assert scorecard.book_return(0, 1, 1) is None


def test_read_rows_skips_a_torn_line(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text(json.dumps(outcome("smc-A-1", 1.0, 1.0)) + "\n{torn", encoding="utf-8")
    assert len(scorecard.read_rows(log)) == 1
    assert scorecard.read_rows(tmp_path / "missing.jsonl") == []
