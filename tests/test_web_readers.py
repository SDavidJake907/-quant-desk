"""Dashboard readers: parse the files the scheduled jobs write, read-only."""

from __future__ import annotations

import json

from scanner.web import readers

STANDINGS = """# Competition standings

## 2026-09

| Competitor | Return |
|---|---|
| You (picks) | +1.00% |

**Head to head: You (picks).**

## 2026-10 (in progress)

| Competitor | Return |
|---|---|
| You (picks) | -6.09% |
| Gemma | +0.46% |
| Ghost-you (random 8) | -0.95% |
| Ghost-Gemma (random 5) | -0.89% |
| Momentum rule | -20.09% |
| S&P 500 | -1.82% |

**Head to head: Gemma.** You (picks) did not beat its ghost; Gemma beat its ghost.

## Quarters

- 2026-Q4: months won -- you 0, Gemma 1; leader Gemma

## Season 2026

You -6.09%, Gemma +0.46% (sum of monthly returns).

Probability Gemma is really the better trader so far: 86% (you: 14%), from the daily returns resampled.
"""


def test_parse_standings_latest_month():
    out = readers.parse_standings(STANDINGS)
    latest = out["latest"]
    assert latest["month"] == "2026-10" and latest["in_progress"]
    returns = {r["key"]: r["return_pct"] for r in latest["rows"]}
    assert returns == {"you": -6.09, "gemma": 0.46, "ghost_you": -0.95,
                       "ghost_gemma": -0.89, "momentum": -20.09, "spx": -1.82}
    assert latest["verdict"].startswith("Head to head: Gemma.")
    assert len(out["months"]) == 2
    assert out["quarters"] == ["2026-Q4: months won -- you 0, Gemma 1; leader Gemma"]
    assert out["probability"] == {"gemma": 0.86, "you": 0.14}


def test_parse_standings_before_probability_exists():
    text = STANDINGS.replace(
        "Probability Gemma is really the better trader so far: 86% (you: 14%), "
        "from the daily returns resampled.",
        "How sure is the lead? Needs 10 trading days of snapshots.")
    out = readers.parse_standings(text)
    assert out["probability"] is None
    assert out["probability_note"].startswith("How sure is the lead?")


def test_parse_standings_empty():
    out = readers.parse_standings("")
    assert out["latest"] is None and out["months"] == []


def test_parse_selection():
    text = ("# Selection skill\n\n## 5 sessions ahead\n\n- **Gemma**, chosen: -0.44\n"
            "- **TradingAgents**, rank: -0.25\n\n## 21 sessions ahead\n\n- **Gemma**, none\n")
    out = readers.parse_selection(text)
    assert [g["horizon"] for g in out] == ["5 sessions ahead", "21 sessions ahead"]
    assert out[0]["lines"] == ["Gemma, chosen: -0.44", "TradingAgents, rank: -0.25"]


def test_pl_series_latest_rerun_wins(tmp_path):
    snaps = tmp_path / "snapshots.jsonl"
    rows = [
        {"day": "2026-10-08", "book": "picks", "pl_total": 10.0, "invested": 1},
        {"day": "2026-10-08", "book": "picks", "pl_total": 12.5, "invested": 1},
        {"day": "2026-10-07", "book": "gemma", "pl_total": -3.0, "invested": 1},
        {"day": "2026-10-07", "book": "momentum", "pl_total": 99.0, "invested": 1},
    ]
    snaps.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    out = readers.pl_series(snaps)
    assert out == {"picks": [{"day": "2026-10-08", "pl": 12.5}],
                   "gemma": [{"day": "2026-10-07", "pl": -3.0}]}


def test_freeze_header():
    text = ("# Evaluation window\n\n    window               34  (window 33 closed)\n"
            "    policy fingerprint   1c5ed20750e6  (extended)\n    code build           4b77fab  -- x\n")
    assert readers.freeze_header(text) == {"window": "34", "fingerprint": "1c5ed20750e6",
                                           "build": "4b77fab"}


def test_watchlist_symbols(tmp_path):
    p = tmp_path / "picks.txt"
    p.write_text("# header\n\nnvda\nXOM  # energy\n", encoding="utf-8")
    assert readers.watchlist_symbols(p) == ["NVDA", "XOM"]
    assert readers.watchlist_symbols(tmp_path / "missing.txt") == []


def test_activity_filters_and_hides_test_orders(tmp_path):
    log = tmp_path / "audit.jsonl"
    rows = [
        {"at": "1", "event": "order_submitted", "symbol": "XOM",
         "intent": {"client_order_id": "pck-XOM-1"}},
        {"at": "2", "event": "tradingagents_rating", "symbol": "AMD"},
        {"at": "3", "event": "order_submitted", "symbol": "TEST",
         "intent": {"client_order_id": "smc-TEST-1792298365"}},
        {"at": "4", "event": "gemma_exit", "symbol": "MU", "reason": "dropped"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = readers.activity(log)
    assert [(e["at"], e["book"]) for e in out] == [("4", "gemma"), ("1", "picks")]


def test_group_positions_by_book():
    orders = [
        {"symbol": "XOM", "side": "buy", "filled_at": "2026-10-08T14:00:00Z",
         "client_order_id": "pck-XOM-1"},
        {"symbol": "MU", "side": "buy", "filled_at": "2026-10-09T14:00:00Z",
         "client_order_id": "gem-MU-1"},
        {"symbol": "CCL", "side": "buy", "filled_at": "2026-10-09T14:00:00Z",
         "client_order_id": "man-CCL-1"},
    ]
    positions = [
        {"symbol": "XOM", "qty": "10", "avg_entry_price": "100", "current_price": "110",
         "market_value": "1100", "unrealized_pl": "100", "unrealized_plpc": "0.1"},
        {"symbol": "MU", "qty": "5", "market_value": "500"},
        {"symbol": "CCL", "qty": "1", "market_value": "22"},
        {"symbol": "ZZZ", "qty": "1", "market_value": "5"},
    ]
    open_orders = [{"symbol": "XOM", "side": "sell", "type": "stop", "stop_price": "95.5"}]
    books = readers.group_positions(positions, orders, open_orders)
    assert [r["symbol"] for r in books["picks"]] == ["XOM", "CCL"]
    assert books["picks"][0]["stop"] == 95.5
    assert round(books["picks"][0]["pl_pct"], 6) == 10.0
    assert [r["symbol"] for r in books["gemma"]] == ["MU"]
    assert [r["symbol"] for r in books["other"]] == ["ZZZ"]


def test_app_has_no_write_endpoints():
    from scanner.web.app import app
    methods = {m for route in app.routes for m in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}


def test_activity_summaries_for_outcomes_and_decisions(tmp_path):
    log = tmp_path / "audit.jsonl"
    rows = [
        {"at": "1", "event": "trade_outcome", "symbol": "PLTR", "intent": None,
         "outcome": {"client_order_id": "pck-PLTR-1", "realized_dollars": -71.95,
                     "realized_r": -0.3953, "exit_reason": "flattened"}},
        {"at": "2", "event": "gemma_decision", "symbol": "",
         "reason": json.dumps({"keep": ["MPC", "MU"], "buys": [], "sells": ["VLO"]})},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    decision, outcome = readers.activity(log)
    assert outcome["book"] == "picks"
    assert outcome["reason"] == "-$72 · -0.40R · flattened"
    assert outcome["realized"] == -71.95
    assert decision["book"] == "gemma"
    assert decision["reason"] == "keep MPC, MU · buy — · sell VLO"
