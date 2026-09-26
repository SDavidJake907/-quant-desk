"""Memory built from outcomes, and its refusal to generalise from one trade.

Two retrieval-style memories were tested on 2026-09-24 and both failed: Mem0
ranked a deal ANNOUNCEMENT above its TERMINATION and talked qwen2.5:14b out of
a correct answer. Memory built from text inherits whatever the text says,
including things that stopped being true.

These pin the properties that make an outcome-grounded memory safer: it is
built from realized R, it refuses to report a statistic it cannot support,
and its one low-sample rule is a rule rather than a prediction.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from scanner.llm import memory

UTC = timezone.utc


def _log(tmp_path, trades):
    path = tmp_path / "audit.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for symbol, r, when in trades:
            handle.write(json.dumps({
                "event": "trade_outcome",
                "at": when.isoformat(),
                "policy": "fb709122e949",
                "outcome": {"symbol": symbol, "realized_r": r,
                            "client_order_id": f"smc-{symbol}-{r}"},
            }) + "\n")
    return path


def test_one_trade_is_not_a_track_record(tmp_path):
    """THE POINT. Ten trades across ten symbols is n=1 each, and a mean from
    one observation is noise wearing a number."""
    path = _log(tmp_path, [("ACN", 2.1, datetime(2026, 9, 1, tzinfo=UTC))])
    assert memory.records(path) == ()
    assert "no per-symbol history" in memory.build_block(path)


def test_a_record_appears_once_there_is_enough_of_it(tmp_path):
    path = _log(tmp_path, [("ACN", r, datetime(2026, 9, 1, tzinfo=UTC))
                           for r in (1.0, -1.0, 2.0, -1.0, 1.5)])
    got = memory.records(path)
    assert [r.symbol for r in got] == ["ACN"]
    assert got[0].trades == 5 and got[0].wins == 3


def test_the_block_says_when_the_record_cannot_tell_profit_from_chance(tmp_path):
    path = _log(tmp_path, [("A", 3.2, datetime(2026, 9, 1, tzinfo=UTC)),
                           ("B", -1.0, datetime(2026, 9, 2, tzinfo=UTC)),
                           ("C", -1.0, datetime(2026, 9, 3, tzinfo=UTC))])
    assert "contains zero" in memory.build_block(path)


def test_a_loss_starts_a_cooldown(tmp_path):
    when = datetime(2026, 9, 25, 14, tzinfo=UTC)
    path = _log(tmp_path, [("PSX", -0.47, when)])
    assert memory.in_cooldown(path, "PSX", now=when + timedelta(hours=1))
    assert not memory.in_cooldown(path, "PSX", now=when + timedelta(hours=30))


def test_a_win_does_not_start_a_cooldown(tmp_path):
    """Avoiding a name because it PAID would quietly filter out whatever is
    working."""
    when = datetime(2026, 9, 25, 14, tzinfo=UTC)
    path = _log(tmp_path, [("ACN", 2.16, when)])
    assert not memory.in_cooldown(path, "ACN", now=when + timedelta(hours=1))


def test_cooldown_is_case_insensitive(tmp_path):
    when = datetime(2026, 9, 25, 14, tzinfo=UTC)
    path = _log(tmp_path, [("PSX", -0.47, when)])
    assert memory.in_cooldown(path, "psx", now=when + timedelta(hours=1))


def test_an_untraded_symbol_is_never_in_cooldown(tmp_path):
    path = _log(tmp_path, [("PSX", -0.47, datetime(2026, 9, 25, tzinfo=UTC))])
    assert not memory.in_cooldown(path, "NVDA")


def test_no_history_produces_an_honest_block_not_an_empty_one(tmp_path):
    path = tmp_path / "missing.jsonl"
    assert "no completed trades" in memory.build_block(path)


def test_damaged_lines_do_not_lose_the_rest(tmp_path):
    path = _log(tmp_path, [("ACN", 2.1, datetime(2026, 9, 1, tzinfo=UTC))])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ not json\n")
    assert len(memory.outcomes(path)) == 1


def test_outcomes_can_be_narrowed_to_one_policy(tmp_path):
    path = _log(tmp_path, [("ACN", 2.1, datetime(2026, 9, 1, tzinfo=UTC))])
    assert memory.outcomes(path, policy="fb709122e949")
    assert memory.outcomes(path, policy="somethingelse") == []
