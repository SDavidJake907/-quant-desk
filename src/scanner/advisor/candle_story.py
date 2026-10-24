"""The last daily candles in words for Gemma's stock_story (operator 2026-10-25: "lets give the candles some color").

Gemma's choices (2026-10-25): the last 5 days, WITH labels -- GREEN / RED (close vs open), BIG (body larger than
1 daily ATR), GAP UP / GAP DOWN (open 2%+ from the prior close) -- plus her two additions: the candle's range vs
the day before (higher high, lower low, inside day, outside day) and volume in words (HIGH / AVG / LOW) next to
its multiple of the 20-day average. "A BIG red candle with a long upper wick on 3.1x volume is a signal of a
climax ... the labels let me identify the quality of the momentum."
Lab evidence she already has: after a run-up a BIG red candle was usually followed by a bounce; candle patterns
as buy signals did not beat momentum. Information only. PURE.
"""

from __future__ import annotations

from collections.abc import Sequence

from scanner.features.bars import Bars

DAYS = 5
GAP_PCT = 2.0
VOLUME_DAYS = 20
HIGH_VOLUME, LOW_VOLUME = 1.5, 0.7


def _range_word(high: float, low: float, prev_high: float, prev_low: float) -> str:
    up, down = high > prev_high, low < prev_low
    return ("outside day" if up and down else "higher high" if up else "lower low" if down else "inside day")


def _volume_word(ratio: float) -> str:
    return "HIGH" if ratio >= HIGH_VOLUME else "LOW" if ratio <= LOW_VOLUME else "AVG"


def candle(i: int, b: Bars, atr_pct: float | None) -> str:
    o, h, l, c, prev = b.opens[i], b.highs[i], b.lows[i], b.closes[i], b.closes[i - 1]
    body = abs(c - o) / c * 100
    words = [f"{b.dates[i]:%a} {'GREEN' if c >= o else 'RED'} {(c / prev - 1) * 100:+.1f}%"]
    gap = (o / prev - 1) * 100
    if abs(gap) >= GAP_PCT:
        words.append(f"GAP {'UP' if gap > 0 else 'DOWN'} {gap:+.1f}%")
    if atr_pct and body > atr_pct:
        words.append("BIG")
    detail = [f"body {body:.1f}%", f"wicks up {(h - max(o, c)) / c * 100:.1f}% / down {(min(o, c) - l) / c * 100:.1f}%"]
    window: Sequence[float] = b.volumes[max(0, i - VOLUME_DAYS):i]
    if window and sum(window):
        ratio = b.volumes[i] / (sum(window) / len(window))
        detail.append(f"vol {ratio:.1f}x {_volume_word(ratio)}")
    detail.append(_range_word(h, l, b.highs[i - 1], b.lows[i - 1]))
    return " ".join(words) + f" ({', '.join(detail)})"


def text(b: Bars, atr_pct: float | None, days: int = DAYS) -> str:
    """'Last 5 days: Mon GREEN +2.1% (...) | ...' oldest first; empty without enough history."""
    n = len(b.closes)
    if n < days + 1:
        return ""
    return f"Last {days} days: " + " | ".join(candle(i, b, atr_pct) for i in range(n - days, n))
