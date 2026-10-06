"""The pattern card for a live position: detect on the last 38 daily closes,
attach the MEASURED record from data/factors/pattern_stats.json.

Measured 2026-10-06 (research_gpu/pattern_lab.py, 143,586 events): no pattern
has a measured edge, and "bullish" and "bearish" ones lead to the same
returns. So the card's job today is protective: it tells Gemma, with numbers,
that a pattern's NAME carries no direction, so trading folklore does not leak
into its read. If a future run marks one MEASURED, the same card carries it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from scanner.patterns import detect as D

STATS = Path("data/factors/pattern_stats.json")
LOOKBACK_AGE = 10


def load_stats(path: Path = STATS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["patterns"] if path.exists() else {}


def _atr(highs, lows, closes, n=20) -> float:
    trs = [max(h - lo, abs(h - c0), abs(lo - c0)) for h, lo, c0 in zip(highs[1:], lows[1:], closes[:-1])]
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else 0.0


def current(closes: Sequence[float], highs: Sequence[float], lows: Sequence[float],
            stats: dict) -> str | None:
    """The card for the newest window, or None when no pattern is present.
    `age` counts the days the same pattern has been showing (up to 10)."""
    if len(closes) < D.WINDOW + 1:
        return None
    code, ext = D.detect(list(closes[-D.WINDOW:]))
    if code is None:
        return None
    age = 0
    for back in range(1, LOOKBACK_AGE + 1):
        if len(closes) - back < D.WINDOW or D.detect(list(closes[-D.WINDOW - back:-back]))[0] != code:
            break
        age = back
    prices = [p for _, p, _ in ext]
    lows_ext = [p for _, p, k in ext if k == "min"] or prices
    highs_ext = [p for _, p, k in ext if k == "max"] or prices
    bullish_shape = code in ("IHS", "BBOT", "TBOT", "RBOT", "DBOT")
    neckline = (sum(highs_ext) / len(highs_ext)) if bullish_shape else (sum(lows_ext) / len(lows_ext))
    atr = _atr(highs, lows, closes) or 1.0
    s = stats.get(code) or {"ev20": 0.0, "t": 0.0, "n": 0, "verdict": "no measured edge"}
    return D.card(code=code, age=age, neckline_atr=(closes[-1] - neckline) / atr,
                  height_atr=(max(prices) - min(prices)) / atr, stats=s)
