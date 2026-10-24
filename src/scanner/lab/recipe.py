"""The recipe Gemma writes, and the menu it is written from.

A fixed menu, not free code: she cannot run code, and a closed menu keeps the
number of possible variations countable (every try is a trial: with enough
tries one looks good by luck). Her additions on 2026-10-23 are in: distance
from the 20-day average, ADX, 10-day relative volume, Bollinger width, gap and
close-in-range, and a per-sector cap.

PURE: dicts in, a validated Recipe or a plain-English error out.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from scanner.models import Frozen

#: Numeric indicators: the values a filter may compare against, and what each means.
NUMERIC: dict[str, tuple[tuple[float, ...], str]] = {
    "price_vs_sma20_pct": ((-10, -5, -2, 0, 2, 5, 10), "% above (+) or below (-) the 20-day average"),
    "price_vs_sma50_pct": ((-10, -5, -2, 0, 2, 5, 10), "% vs the 50-day average"),
    "price_vs_sma100_pct": ((-10, -5, -2, 0, 2, 5, 10), "% vs the 100-day average"),
    "price_vs_sma200_pct": ((-10, -5, -2, 0, 2, 5, 10), "% vs the 200-day average"),
    "rsi14": ((20, 30, 40, 50, 60, 70, 80), "RSI(14)"),
    "ret_1m_pct": ((-20, -10, -5, 0, 5, 10, 20, 30), "1-month return %"),
    "ret_3m_pct": ((-20, -10, -5, 0, 5, 10, 20, 30), "3-month return %"),
    "mom_12_1_pct": ((-20, 0, 10, 20, 40), "12-1 momentum % (12 months, skipping the last)"),
    "pct_below_52w_high": ((5, 10, 20, 30), "% under the 52-week high"),
    "volume_vs_20d": ((0.5, 1, 1.5, 2, 3), "today's volume / prior 20-day average"),
    "volume_vs_10d": ((0.5, 1, 1.5, 2, 3), "today's volume / prior 10-day average"),
    "atr_pct": ((1, 2, 3, 4, 6), "daily ATR as % of price"),
    "adx14": ((15, 20, 25, 30, 40), "ADX(14) trend strength"),
    "bb_width_pct": ((3, 5, 8, 12, 20), "Bollinger width (4 st.dev of 20 days) as % of price; low = squeeze"),
    "gap_pct": ((-4, -2, -1, 0, 1, 2, 4), "today's open vs yesterday's close %"),
    "close_in_range": ((0.25, 0.5, 0.75, 0.9), "where the close sits in the day's range (1 = at the high)"),
    # The second menu (operator 2026-10-23: "give her all the indicators ... market hours,
    # after hours, days of the week, months").
    "macd_hist_pct": ((-1, -0.5, 0, 0.5, 1), "MACD(12,26,9) histogram as % of price"),
    "stoch_k14": ((10, 20, 50, 80, 90), "Stochastic %K(14)"),
    "mfi14": ((20, 30, 50, 70, 80), "Money Flow Index(14) (RSI weighted by volume)"),
    "ret_1w_pct": ((-10, -5, -2, 0, 2, 5, 10), "1-week return %"),
    "ret_6m_pct": ((-20, 0, 10, 20, 40, 60), "6-month return %"),
    "pct_above_52w_low": ((10, 25, 50, 100), "% above the 52-week low"),
    "bb_pct_b": ((0, 0.2, 0.5, 0.8, 1), "Bollinger %B (0 = lower band, 1 = upper band)"),
    "atr_vs_50d": ((0.7, 0.9, 1.1, 1.3, 1.5), "ATR / its 50-day average (above 1 = volatility expanding)"),
    "streak": ((-4, -3, -2, -1, 1, 2, 3, 4), "consecutive up closes (+) or down closes (-)"),
    "rs_3m_pct": ((-10, -5, 0, 5, 10, 20), "3-month return minus the S&P 500's (relative strength)"),
    "intraday_pct": ((-3, -1, 0, 1, 3), "REGULAR SESSION today: close vs open %"),
    "intraday_5d_pct": ((-5, -2, 0, 2, 5), "regular-session moves summed over 5 days %"),
    "overnight_5d_pct": ((-5, -2, 0, 2, 5),
                         "OVERNIGHT moves (after-hours + premarket: close to next open) summed over 5 days %"),
    "day_of_week": ((1, 2, 3, 4, 5), "decision day: 1 Monday ... 5 Friday (the buy is the next trading day)"),
    "month": ((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12), "decision month: 1 January ... 12 December"),
    "day_of_month": ((1, 5, 10, 15, 20, 25, 28), "calendar day of the month"),
}
#: Calendar filters also take op "is" (exactly that day / month).
CALENDAR = ("day_of_week", "month", "day_of_month")
#: Yes/no conditions.
FLAGS: dict[str, str] = {
    "new_high_20": "closed above the highest high of the prior 20 days",
    "new_high_55": "closed above the highest high of the prior 55 days",
    "pullback": "below the 20-day average while above the 200-day average",
    "market_uptrend": "the S&P 500 is above its 200-day average",
    "macd_above_signal": "the MACD line is above its signal line",
    "obv_rising": "On-Balance Volume is above its 20-day average",
    "supertrend_up": "SuperTrend(10, 3) is up",
}
TOP_N = (3, 5, 10, 20)
REBALANCE = (5, 10, 21)
STOP_ATR = (0.0, 1.5, 2.5, 3.5)
TARGET_R = (0.0, 2.0, 3.0, 5.0)
MAX_HOLD = (5, 10, 21, 63)
MAX_PER_SECTOR = (0, 1, 2, 3)
MAX_FILTERS = 6


class Filter(Frozen):
    name: str
    op: str = "is"          # above / below for NUMERIC; "is" for FLAGS
    value: float = 1.0


class Recipe(Frozen):
    filters: tuple[Filter, ...]
    rank_by: str
    rank_order: str          # high_first / low_first
    top_n: int
    rebalance_days: int
    stop_atr: float
    target_r: float
    max_hold_days: int
    max_per_sector: int = 0

    @property
    def key(self) -> str:
        """Stable id: the same recipe is the same trial, however it is worded."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:10]

    def describe(self) -> str:
        conds = "; ".join(f.name if f.name in FLAGS else f"{f.name} {f.op} {f.value:g}"
                          for f in self.filters) or "no filters"
        exits = ", ".join(x for x in (
            f"stop {self.stop_atr:g} ATR" if self.stop_atr else "no stop",
            f"target {self.target_r:g}R" if self.target_r else "no target",
            f"max {self.max_hold_days}d"))
        sector = f", max {self.max_per_sector}/sector" if self.max_per_sector else ""
        return (f"[{conds}] rank {self.rank_by} {self.rank_order}, top {self.top_n} every "
                f"{self.rebalance_days}d; {exits}{sector}")


def parse(raw: Any) -> Recipe:
    """A Recipe, or ValueError naming exactly what is off the menu."""
    if not isinstance(raw, dict):
        raise ValueError("the recipe must be a JSON object")
    filters = []
    for f in raw.get("filters") or []:
        name = str(f.get("name", ""))
        if name in FLAGS:
            filters.append(Filter(name=name))
        elif name in NUMERIC:
            op, value = str(f.get("op", "")), f.get("value")
            if op not in ("above", "below") and not (op == "is" and name in CALENDAR):
                raise ValueError(f"filter {name}: op must be 'above' or 'below'" + (" or 'is'" if name in CALENDAR else ""))
            allowed = NUMERIC[name][0]
            if not isinstance(value, (int, float)) or float(value) not in allowed:
                raise ValueError(f"filter {name}: value must be one of {list(allowed)}")
            filters.append(Filter(name=name, op=op, value=float(value)))
        else:
            raise ValueError(f"unknown filter {name!r}")
    if len(filters) > MAX_FILTERS:
        raise ValueError(f"at most {MAX_FILTERS} filters")
    rank_by = str(raw.get("rank_by", ""))
    if rank_by not in NUMERIC:
        raise ValueError(f"rank_by must be a numeric indicator: {sorted(NUMERIC)}")
    order = str(raw.get("rank_order", ""))
    if order not in ("high_first", "low_first"):
        raise ValueError("rank_order must be 'high_first' or 'low_first'")

    def pick(key: str, allowed: tuple) -> Any:
        value = raw.get(key)
        if not isinstance(value, (int, float)) or value not in allowed:
            raise ValueError(f"{key} must be one of {list(allowed)}")
        return value

    recipe = Recipe(filters=tuple(sorted(filters, key=lambda f: (f.name, f.op, f.value))),
                    rank_by=rank_by, rank_order=order,
                    top_n=int(pick("top_n", TOP_N)), rebalance_days=int(pick("rebalance_days", REBALANCE)),
                    stop_atr=float(pick("stop_atr", STOP_ATR)), target_r=float(pick("target_r", TARGET_R)),
                    max_hold_days=int(pick("max_hold_days", MAX_HOLD)),
                    max_per_sector=int(pick("max_per_sector", MAX_PER_SECTOR)))
    if recipe.target_r and not recipe.stop_atr:
        raise ValueError("a target in R needs a stop (R is the stop distance)")
    return recipe


def menu_text() -> str:
    lines = [f"FILTERS (all must hold on the decision day; up to {MAX_FILTERS}). Daily bars: the OVERNIGHT move "
             "(after-hours + premarket) is yesterday's close to today's open; the REGULAR SESSION is open to close. "
             f"Calendar filters ({', '.join(CALENDAR)}) also take op 'is' (exactly that value):"]
    lines += [f"- {k} above/below one of {list(v)}: {d}" for k, (v, d) in NUMERIC.items()]
    lines += [f"- {k} (yes/no): {d}" for k, d in FLAGS.items()]
    lines += ["RANK BY any numeric indicator above, rank_order high_first or low_first.",
              f"top_n {list(TOP_N)}; rebalance_days {list(REBALANCE)} (trading days between buys);",
              f"stop_atr {list(STOP_ATR)} daily ATRs under the buy (0 = none); target_r {list(TARGET_R)} "
              "x the stop distance (0 = none; needs a stop);",
              f"max_hold_days {list(MAX_HOLD)}; max_per_sector {list(MAX_PER_SECTOR)} (0 = no cap)."]
    return "\n".join(lines)


def schema() -> dict[str, Any]:
    """Ollama's constrained-JSON schema for one recipe plus her reason."""
    names = sorted([*NUMERIC, *FLAGS])
    return {"type": "object", "properties": {
        "idea": {"type": "string"},
        "recipe": {"type": "object", "properties": {
            "filters": {"type": "array", "maxItems": MAX_FILTERS, "items": {
                "type": "object", "properties": {
                    "name": {"type": "string", "enum": names},
                    "op": {"type": "string", "enum": ["above", "below", "is"]},
                    "value": {"type": "number"}},
                "required": ["name", "op", "value"]}},
            "rank_by": {"type": "string", "enum": sorted(NUMERIC)},
            "rank_order": {"type": "string", "enum": ["high_first", "low_first"]},
            "top_n": {"type": "integer", "enum": list(TOP_N)},
            "rebalance_days": {"type": "integer", "enum": list(REBALANCE)},
            "stop_atr": {"type": "number", "enum": list(STOP_ATR)},
            "target_r": {"type": "number", "enum": list(TARGET_R)},
            "max_hold_days": {"type": "integer", "enum": list(MAX_HOLD)},
            "max_per_sector": {"type": "integer", "enum": list(MAX_PER_SECTOR)}},
            "required": ["filters", "rank_by", "rank_order", "top_n", "rebalance_days",
                         "stop_atr", "target_r", "max_hold_days", "max_per_sector"]}},
        "required": ["idea", "recipe"]}
