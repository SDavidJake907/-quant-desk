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
    # Volume in detail (operator 2026-10-23: "backtest even with volume. and time").
    "volume_trend": ((0.7, 0.9, 1.1, 1.3, 1.5), "20-day average volume / 60-day (above 1 = volume rising)"),
    "up_volume_ratio": ((0.5, 0.8, 1, 1.25, 1.5, 2),
                        "volume on up days / volume on down days, last 20 days (above 1 = accumulation)"),
    "close_vs_vwap20_pct": ((-5, -2, 0, 2, 5), "close vs the 20-day volume-weighted average price %"),
    "dollar_volume_m": ((10, 25, 50, 100, 250), "average daily dollar volume, $ millions (liquidity)"),
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
    "ema10_above_ema50": "the 10-day EMA is above the 50-day EMA",
    "ema_cross_up_5d": "the 10-day EMA crossed above the 50-day EMA within the last 5 days",
    "golden_cross": "the 50-day average is above the 200-day average",
    "golden_cross_5d": "the 50-day crossed above the 200-day within the last 5 days",
    "psar_up": "Parabolic SAR (0.02 / 0.2) is under the price (uptrend)",
    "psar_flip_up_5d": "Parabolic SAR flipped to an uptrend within the last 5 days",
}
TOP_N = (3, 5, 10, 20)
REBALANCE = (5, 10, 21)
STOP_ATR = (0.0, 1.5, 2.5, 3.5)
TARGET_R = (0.0, 2.0, 3.0, 5.0)
MAX_HOLD = (5, 10, 21, 63)
MAX_PER_SECTOR = (0, 1, 2, 3)
MAX_FILTERS = 6


#: SELL RULES (operator 2026-10-23: "buy/sell indicators in details"): up to this many
#: indicator conditions; a close where ANY one is true sells.
MAX_EXIT_FILTERS = 3
TRAIL_ATR = (0.0, 2.0, 3.0, 4.0)
#: When the buy fills after the decision close: the next day's open or its close.
ENTRY_AT = ("next_close", "next_open")


class Filter(Frozen):
    name: str
    op: str = "is"          # above / below (/ is for CALENDAR) for NUMERIC; is / not for FLAGS
    value: float = 1.0

    def describe(self) -> str:
        if self.name in FLAGS:
            return self.name if self.op != "not" else f"NOT {self.name}"
        return f"{self.name} {self.op} {self.value:g}"


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
    exit_filters: tuple[Filter, ...] = ()
    trail_atr: float = 0.0
    entry_at: str = "next_close"

    @property
    def key(self) -> str:
        """Stable id: the same recipe is the same trial, however it is worded. Fields at
        their defaults are left out, so recipes from before the sell rules keep their key."""
        dump = self.model_dump(mode="json")
        for name, default in (("exit_filters", []), ("trail_atr", 0.0), ("entry_at", "next_close")):
            if dump.get(name) == default:
                dump.pop(name)
        blob = json.dumps(dump, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:10]

    def describe(self) -> str:
        conds = "; ".join(f.describe() for f in self.filters) or "no filters"
        exits = ", ".join(x for x in (
            f"stop {self.stop_atr:g} ATR" if self.stop_atr else "no stop",
            f"target {self.target_r:g}R" if self.target_r else "no target",
            f"trail {self.trail_atr:g} ATR under the highest close" if self.trail_atr else "",
            ("sell at a close when " + " OR ".join(f.describe() for f in self.exit_filters))
            if self.exit_filters else "",
            f"max {self.max_hold_days}d") if x)
        sector = f", max {self.max_per_sector}/sector" if self.max_per_sector else ""
        entry = ", buy at the next open" if self.entry_at == "next_open" else ""
        return (f"[{conds}] rank {self.rank_by} {self.rank_order}, top {self.top_n} every "
                f"{self.rebalance_days}d{entry}; {exits}{sector}")


def _filters(items: Any, label: str) -> list[Filter]:
    """Menu conditions; an off-menu number snaps to the nearest allowed value."""
    out = []
    for f in items or []:
        name = str(f.get("name", ""))
        if name in FLAGS:
            op = str(f.get("op", "is"))
            out.append(Filter(name=name, op="not" if op == "not" else "is"))
        elif name in NUMERIC:
            op, value = str(f.get("op", "")), f.get("value")
            if op not in ("above", "below") and not (op == "is" and name in CALENDAR):
                raise ValueError(f"{label} {name}: op must be 'above' or 'below'"
                                 + (" or 'is'" if name in CALENDAR else ""))
            allowed = NUMERIC[name][0]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{label} {name}: value must be a number from {list(allowed)}")
            # Off the menu (RSI 35): the nearest allowed value, not a lost try (2026-10-23: a third
            # of her first session was refused this way). describe() shows what actually ran.
            value = min(allowed, key=lambda a: (abs(a - float(value)), a))
            out.append(Filter(name=name, op=op, value=float(value)))
        else:
            raise ValueError(f"unknown {label} {name!r}")
    return sorted(out, key=lambda f: (f.name, f.op, f.value))


def parse(raw: Any) -> Recipe:
    """A Recipe, or ValueError naming exactly what is off the menu."""
    if not isinstance(raw, dict):
        raise ValueError("the recipe must be a JSON object")
    filters = _filters(raw.get("filters"), "filter")
    exits = _filters(raw.get("exit_filters"), "sell rule")
    if len(exits) > MAX_EXIT_FILTERS:
        raise ValueError(f"at most {MAX_EXIT_FILTERS} sell rules")
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

    recipe = Recipe(filters=tuple(filters),
                    rank_by=rank_by, rank_order=order,
                    top_n=int(pick("top_n", TOP_N)), rebalance_days=int(pick("rebalance_days", REBALANCE)),
                    stop_atr=float(pick("stop_atr", STOP_ATR)), target_r=float(pick("target_r", TARGET_R)),
                    max_hold_days=int(pick("max_hold_days", MAX_HOLD)),
                    max_per_sector=int(pick("max_per_sector", MAX_PER_SECTOR)),
                    exit_filters=tuple(exits),
                    trail_atr=float(pick("trail_atr", TRAIL_ATR)) if "trail_atr" in raw else 0.0,
                    entry_at=str(raw.get("entry_at") or "next_close"))
    if recipe.entry_at not in ENTRY_AT:
        raise ValueError(f"entry_at must be one of {list(ENTRY_AT)}")
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
              f"max_hold_days {list(MAX_HOLD)}; max_per_sector {list(MAX_PER_SECTOR)} (0 = no cap).",
              f"SELL RULES: exit_filters, up to {MAX_EXIT_FILTERS} conditions from the same menu (yes/no ones may "
              "use op 'not', e.g. NOT supertrend_up); a CLOSE where ANY one is true sells. trail_atr "
              f"{list(TRAIL_ATR)}: a close more than that many daily ATRs under the highest close since the "
              "buy sells (0 = off). Stops and targets act during the day; sell rules, the trail and the "
              "time limit at the close.",
              f"TIMING: entry_at {list(ENTRY_AT)} -- buy at the next day's open (holds the overnight move "
              "from the decision) or its close."]
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
                    "op": {"type": "string", "enum": ["above", "below", "is", "not"]},
                    "value": {"type": "number"}},
                "required": ["name", "op", "value"]}},
            "rank_by": {"type": "string", "enum": sorted(NUMERIC)},
            "rank_order": {"type": "string", "enum": ["high_first", "low_first"]},
            "top_n": {"type": "integer", "enum": list(TOP_N)},
            "rebalance_days": {"type": "integer", "enum": list(REBALANCE)},
            "stop_atr": {"type": "number", "enum": list(STOP_ATR)},
            "target_r": {"type": "number", "enum": list(TARGET_R)},
            "max_hold_days": {"type": "integer", "enum": list(MAX_HOLD)},
            "max_per_sector": {"type": "integer", "enum": list(MAX_PER_SECTOR)},
            "exit_filters": {"type": "array", "maxItems": MAX_EXIT_FILTERS, "items": {
                "type": "object", "properties": {
                    "name": {"type": "string", "enum": names},
                    "op": {"type": "string", "enum": ["above", "below", "is", "not"]},
                    "value": {"type": "number"}},
                "required": ["name", "op", "value"]}},
            "trail_atr": {"type": "number", "enum": list(TRAIL_ATR)},
            "entry_at": {"type": "string", "enum": list(ENTRY_AT)}},
            "required": ["filters", "rank_by", "rank_order", "top_n", "rebalance_days",
                         "stop_atr", "target_r", "max_hold_days", "max_per_sector",
                         "exit_filters", "trail_atr", "entry_at"]}},
        "required": ["idea", "recipe"]}
