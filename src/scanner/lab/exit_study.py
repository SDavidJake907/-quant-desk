"""Where to sell (operator 2026-10-23: "its best to hold stocks, until it ready to sell.
well lets find the best spot to sell").

The entry is held fixed -- two of the best buy rules so far -- and only the exit
changes, over all three stock groups, under the lab's rules (idle cash in the index,
costs, random control with the same exits). One exit winning one cell can be luck; an
exit that is better than holding in every group and under both entries is the finding.
Every run is counted (stage "exit_study" in data/lab/log.jsonl).
"""

from __future__ import annotations

from typing import Any

from scanner.lab.baselines import _f, _r

ENTRIES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("live-plan entry", dict(filters=[_f("market_uptrend"), _f("mom_12_1_pct", "above", 20),
                                      _f("ret_6m_pct", "above", 0), _f("price_vs_sma50_pct", "above", 0)],
                             top_n=5, rebalance_days=5, max_per_sector=2)),
    ("momentum 12-1 entry", dict(filters=[], top_n=10, rebalance_days=21)),
)

#: The exit grid. max_hold 252 = "hold until a rule sells" (a year at most).
EXITS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("hold 3 months", dict(max_hold_days=63)),
    ("hold 6 months", dict(max_hold_days=126)),
    ("hold 12 months", dict(max_hold_days=252)),
    ("trail 2 ATR", dict(trail_atr=2.0, max_hold_days=252)),
    ("trail 3 ATR", dict(trail_atr=3.0, max_hold_days=252)),
    ("trail 4 ATR", dict(trail_atr=4.0, max_hold_days=252)),
    ("stop 2.5 + target 2R", dict(stop_atr=2.5, target_r=2.0, max_hold_days=252)),
    ("stop 2.5 + target 3R", dict(stop_atr=2.5, target_r=3.0, max_hold_days=252)),
    ("stop 2.5 + target 5R", dict(stop_atr=2.5, target_r=5.0, max_hold_days=252)),
    ("sell RSI above 80", dict(exit_filters=[_f("rsi14", "above", 80)], max_hold_days=252)),
    ("sell close under 50-day", dict(exit_filters=[_f("price_vs_sma50_pct", "below", 0)], max_hold_days=252)),
    ("sell close under 20-day", dict(exit_filters=[_f("price_vs_sma20_pct", "below", 0)], max_hold_days=252)),
    ("sell SuperTrend down", dict(exit_filters=[_f("supertrend_up", "not")], max_hold_days=252)),
    ("sell 50-day under 200-day", dict(exit_filters=[_f("golden_cross", "not")], max_hold_days=252)),
    ("sell MACD under signal", dict(exit_filters=[_f("macd_above_signal", "not")], max_hold_days=252)),
    ("live-plan exit (stop 2.5 + trail 3)", dict(stop_atr=2.5, trail_atr=3.0, max_hold_days=252)),
    ("trail 3 + close under 50-day", dict(trail_atr=3.0, exit_filters=[_f("price_vs_sma50_pct", "below", 0)],
                                          max_hold_days=252)),
)


def grid() -> list[tuple[str, str, dict[str, Any]]]:
    """(entry name, exit name, recipe dict) for every pair."""
    out = []
    for en, entry in ENTRIES:
        for xn, exit_ in EXITS:
            out.append((en, xn, _r(**{**entry, **exit_})))
    return out
