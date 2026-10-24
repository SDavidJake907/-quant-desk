"""The best entry with the best exit, per gate (operator 2026-10-23: "lets also find the
best entry, with the exit. on all 3 gates").

The exits come from the exit study (lab/exit_study.py): its two best across all six
cells, plus the live plan's own exit. Every entry here gets the SAME book shape -- 10
slots, empty slots filled every 5 trading days -- so the difference is the buy rule
alone. Run on big caps (gate 1), mid caps (gate 2) and small caps (gate 3). Counted
(stage "entry_study"); an entry that wins one cell can be luck, one that is good under
every exit in a group is the finding.
"""

from __future__ import annotations

from typing import Any

from scanner.lab.baselines import _f, _r
from scanner.lab.exit_study import EXITS

_SHAPE = dict(top_n=10, rebalance_days=5)

ENTRIES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("momentum 12-1", dict(filters=[], rank_by="mom_12_1_pct")),
    ("momentum + market filter", dict(filters=[_f("market_uptrend")], rank_by="mom_12_1_pct")),
    ("live-plan entry", dict(filters=[_f("market_uptrend"), _f("mom_12_1_pct", "above", 20),
                                      _f("ret_6m_pct", "above", 0), _f("price_vs_sma50_pct", "above", 0)],
                             rank_by="mom_12_1_pct")),
    ("6-month leaders", dict(filters=[_f("price_vs_sma50_pct", "above", 0)], rank_by="ret_6m_pct")),
    ("relative strength 3m", dict(filters=[_f("rs_3m_pct", "above", 10)], rank_by="rs_3m_pct")),
    ("fresh golden cross", dict(filters=[_f("golden_cross_5d")], rank_by="mom_12_1_pct")),
    ("fresh EMA 10/50 cross", dict(filters=[_f("ema_cross_up_5d")], rank_by="mom_12_1_pct")),
    ("MACD + SuperTrend", dict(filters=[_f("macd_above_signal"), _f("supertrend_up"),
                                        _f("price_vs_sma50_pct", "above", 0)], rank_by="mom_12_1_pct")),
    ("near the 52-week high", dict(filters=[_f("pct_below_52w_high", "below", 5)],
                                   rank_by="pct_below_52w_high", rank_order="low_first")),
    ("55-day breakout on volume", dict(filters=[_f("new_high_55"), _f("volume_vs_20d", "above", 1.5)],
                                       rank_by="volume_vs_20d")),
    ("Coiled Spring (Gemma)", dict(filters=[_f("price_vs_sma50_pct", "above", 0), _f("price_vs_sma200_pct", "above", 0),
                                            _f("bb_width_pct", "below", 5), _f("mom_12_1_pct", "above", 0),
                                            _f("adx14", "above", 20), _f("obv_rising")], rank_by="up_volume_ratio")),
    ("accumulation", dict(filters=[_f("up_volume_ratio", "above", 1.5), _f("obv_rising"),
                                   _f("price_vs_sma50_pct", "above", 0)], rank_by="up_volume_ratio")),
    ("dip in an uptrend", dict(filters=[_f("rsi14", "below", 30), _f("price_vs_sma200_pct", "above", 0),
                                        _f("market_uptrend")], rank_by="rsi14", rank_order="low_first")),
    ("low volatility", dict(filters=[], rank_by="atr_pct", rank_order="low_first")),
)

LIVE_EXIT = "live-plan exit (stop 2.5 + trail 3)"


def exits_by_name() -> dict[str, dict[str, Any]]:
    return dict(EXITS)


def grid(exit_names: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """(entry name, exit name, recipe dict) for every entry under each named exit."""
    exits = exits_by_name()
    return [(en, xn, _r(**{**entry, **_SHAPE, **exits[xn]}))
            for xn in exit_names for en, entry in ENTRIES]


def best_exits(rows: list[dict[str, Any]], n: int = 2) -> list[str]:
    """The exit study's top `n` exits by average annual return over 'hold 3 months',
    across every (group, entry) cell, from its log rows (stage exit_study)."""
    by_cell: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        entry, _, exit_ = str(r.get("name", "")).partition(" / ")
        by_cell.setdefault((str(r.get("universe") or "large"), entry), {})[exit_] = r["result"]["annual_pct"]
    gains: dict[str, list[float]] = {}
    for cell in by_cell.values():
        base = cell.get("hold 3 months")
        if base is None:
            continue
        for x, a in cell.items():
            gains.setdefault(x, []).append(a - base)
    ranked = sorted(gains, key=lambda x: -sum(gains[x]) / len(gains[x]))
    return [x for x in ranked if x != "hold 3 months"][:n]
