"""The desk's classic-strategy library (operator 2026-10-23: "backtest everything").

Every combination cannot be tested -- there are billions, and the best of billions is
luck. These are the well-known families, each written once from the lab's menu, run
under the same rules as Gemma's recipes (idle cash in the index, costs, random control)
on all three stock groups, and shown to her as reference rows. Each run is counted as
a trial. "the live plan" approximates the account's own rules (execution/satellite.py)
on daily bars: the market filter stands in for its group gate, the 3-ATR trail and a
63-day limit for its trailing exit and time stop.
"""

from __future__ import annotations

from typing import Any

_D: dict[str, Any] = {"filters": [], "rank_by": "mom_12_1_pct", "rank_order": "high_first", "top_n": 10,
                      "rebalance_days": 21, "stop_atr": 0.0, "target_r": 0.0, "max_hold_days": 63,
                      "max_per_sector": 0, "exit_filters": [], "trail_atr": 0.0, "entry_at": "next_close"}


def _f(name: str, op: str = "is", value: float = 1.0) -> dict[str, Any]:
    return {"name": name, "op": op, "value": value}


def _r(**kw: Any) -> dict[str, Any]:
    return {**_D, **kw}


BASELINES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("momentum 12-1, monthly", _r()),
    ("momentum 12-1 + market filter", _r(filters=[_f("market_uptrend")])),
    ("momentum + trend + 3 ATR trail", _r(filters=[_f("market_uptrend"), _f("price_vs_sma200_pct", "above", 0)],
                                          trail_atr=3.0)),
    ("52-week-high proximity", _r(filters=[_f("pct_below_52w_high", "below", 5)], rank_by="pct_below_52w_high",
                                  rank_order="low_first")),
    ("relative strength 3m + trail", _r(filters=[_f("rs_3m_pct", "above", 10)], rank_by="rs_3m_pct", trail_atr=3.0)),
    ("low volatility", _r(rank_by="atr_pct", rank_order="low_first", top_n=20)),
    ("short-term reversal (1 week)", _r(filters=[_f("ret_1w_pct", "below", -5), _f("price_vs_sma200_pct", "above", 0)],
                                        rank_by="ret_1w_pct", rank_order="low_first", rebalance_days=5,
                                        max_hold_days=5)),
    ("oversold dip in an uptrend", _r(filters=[_f("rsi14", "below", 30), _f("price_vs_sma200_pct", "above", 0),
                                               _f("market_uptrend")], rank_by="rsi14", rank_order="low_first",
                                      rebalance_days=5, max_hold_days=10,
                                      exit_filters=[_f("rsi14", "above", 50)])),
    ("55-day breakout on volume", _r(filters=[_f("new_high_55"), _f("volume_vs_20d", "above", 1.5)],
                                     rank_by="volume_vs_20d", rebalance_days=5, trail_atr=3.0)),
    ("volatility squeeze breakout", _r(filters=[_f("bb_width_pct", "below", 5), _f("new_high_20")],
                                       rank_by="adx14", rebalance_days=5, stop_atr=2.5, target_r=3.0,
                                       max_hold_days=21)),
    ("accumulation (up-volume, OBV)", _r(filters=[_f("up_volume_ratio", "above", 1.5), _f("obv_rising"),
                                                  _f("price_vs_sma50_pct", "above", 0)],
                                         rank_by="up_volume_ratio", trail_atr=3.0)),
    ("overnight drift", _r(filters=[_f("overnight_5d_pct", "above", 2)], rank_by="overnight_5d_pct",
                           rebalance_days=5, max_hold_days=5, entry_at="next_open")),
    ("turn of the month", _r(filters=[_f("day_of_month", "above", 25), _f("market_uptrend")],
                             rebalance_days=5, max_hold_days=5)),
    ("MACD + SuperTrend trend ride", _r(filters=[_f("macd_above_signal"), _f("supertrend_up"),
                                                 _f("price_vs_sma50_pct", "above", 0)],
                                        rebalance_days=10, exit_filters=[_f("supertrend_up", "not")])),
    ("the live plan (approximation)", _r(filters=[_f("market_uptrend"), _f("mom_12_1_pct", "above", 20),
                                                  _f("ret_6m_pct", "above", 0), _f("price_vs_sma50_pct", "above", 0)],
                                         top_n=5, rebalance_days=5, stop_atr=2.5, trail_atr=3.0,
                                         max_per_sector=2)),
)
