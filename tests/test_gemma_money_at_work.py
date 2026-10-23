"""Gemma's scoreboard says how much of her book is working and its whole-book P/L."""

import importlib.util
import sys


def _script():
    spec = importlib.util.spec_from_file_location("gemma_trade_script", "scripts/gemma_trade.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gemma_trade_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_money_at_work_counts_her_positions_slots_and_whole_book_pl():
    g = _script()
    positions = [{"symbol": "MPC", "market_value": "4750", "unrealized_pl": "350"},
                 {"symbol": "VLO", "market_value": "4400", "unrealized_pl": "160"},
                 {"symbol": "XOM", "market_value": "9000", "unrealized_pl": "-900"}]
    owners = {"MPC": "gemma", "VLO": "gemma", "XOM": "picks"}
    text = g.money_at_work(positions, owners, done_dollars=-557.0, sleeve=50_000.0)
    assert "$9,150 of your $50,000 book invested (18%)" in text
    assert "8 of 10 slots empty" in text
    assert "-$47 (-0.09% of the book)" in text
