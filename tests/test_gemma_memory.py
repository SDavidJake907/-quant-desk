"""Gemma's short memory: her own recent notes, each with what happened since.

Built from her records and prices, not from stored text (a text memory made a
model answer worse on 2026-09-24, llm/memory.py): every note she wrote is shown
beside the price change since she wrote it, and today's facts win.
"""

import json
from datetime import date

from scanner.advisor import gemma_memory as gm


def _decision(day, keep, buys=(), sells=(), journal="", submitted=True):
    return {"event": "gemma_decision", "at": f"{day}T05:46:00-08:00", "symbol": "",
            "reason": json.dumps({"keep": list(keep), "buys": list(buys), "sells": list(sells),
                                  "journal": journal, "submitted": submitted})}


def _outcome(day, symbol, r, dollars, why, coid=None):
    return {"event": "trade_outcome", "at": f"{day}T13:30:00-08:00", "symbol": symbol,
            "outcome": {"client_order_id": coid or f"gem-{symbol}-1", "realized_r": r,
                        "realized_dollars": dollars, "exit_reason": why, "symbol": symbol}}


ROWS = [
    _decision("2026-10-14", ["MU"], buys=["MU"], journal="old one"),
    _outcome("2026-10-14", "COHR", -0.97, -358.9, "stop"),
    _outcome("2026-10-08", "TER", -0.26, -97.5, "flattened"),
    _outcome("2026-10-09", "NVDA", 1.2, 300.0, "target", coid="pck-NVDA-1"),
    _decision("2026-10-16", ["MU", "VLO"], sells=["XOM"], journal="Sold XOM, oil weak."),
    _decision("2026-10-17", ["MU", "VLO"], journal="preview only", submitted=False),
    _decision("2026-10-19", ["MU", "VLO", "COHR"], buys=["COHR"], journal="Bought COHR on AI."),
]


def test_closed_trades_are_hers_only_newest_first():
    trades = gm.closed_trades(ROWS)
    assert [t.symbol for t in trades] == ["COHR", "TER"]
    assert trades[0].r == -0.97 and trades[0].why == "stop"


def test_sessions_are_submitted_decisions_newest_first():
    s = gm.sessions(ROWS, limit=2)
    assert [x.day for x in s] == ["2026-10-19", "2026-10-16"]
    assert s[0].buys == ("COHR",) and s[1].sells == ("XOM",)


def test_the_block_pairs_notes_with_what_happened_since():
    prices = {"XOM": {"2026-10-16": 100.0, "now": 104.0},
              "STX": {"2026-10-19": 50.0, "now": 49.0}}
    review = {"day": "2026-10-19",
              "positions": [{"symbol": "VLO", "verdict": "watch", "reason": "earnings 10/21"}],
              "watchlist": [{"symbol": "STX", "why": "AI storage", "buy_if": "holds 1m lows"}]}
    text = gm.block(ROWS, review=review, change_since=gm.change_lookup(prices),
                    held=["MU", "VLO", "COHR"], today=date(2026, 10, 20))
    assert "SHORT MEMORY" in text and "today's facts below win" in text.lower()
    assert "COHR" in text and "-0.97R" in text and "stop" in text
    # She is holding a name that stopped her out recently: said plainly.
    assert "you hold COHR again" in text
    assert "sold XOM" in text and "+4.0% since" in text
    assert "STX" in text and "-2.0% since" in text and "holds 1m lows" in text
    assert "VLO: watch" in text
    assert "Bought COHR on AI." in text
    assert len(text) <= gm.MAX_CHARS


def test_an_empty_history_says_so():
    text = gm.block([], review=None, change_since=gm.change_lookup({}), held=[],
                    today=date(2026, 10, 20))
    assert "no history yet" in text


def test_missing_prices_are_left_out_not_guessed():
    lookup = gm.change_lookup({"XOM": {"now": 104.0}})
    assert lookup("XOM", "2026-10-16") is None
    assert lookup("ZZZ", "2026-10-16") is None


def test_the_block_is_capped():
    rows = [_decision(f"2026-10-{d:02d}", ["MU"], journal="x" * 900) for d in range(1, 29)]
    text = gm.block(rows, review=None, change_since=gm.change_lookup({}), held=["MU"],
                    today=date(2026, 10, 29))
    assert len(text) <= gm.MAX_CHARS


def test_symbols_to_price_cover_sells_and_watchlist():
    review = {"day": "2026-10-19", "watchlist": [{"symbol": "STX"}], "positions": []}
    wanted = gm.symbols_to_price(ROWS, review=review)
    assert {"XOM", "STX"} <= wanted
