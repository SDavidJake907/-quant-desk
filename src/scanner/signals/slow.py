"""Daily and weekly oversold-buying books (TRIALS #94 -> forward test #95, operator 2026-10-22: "Yes").

#94's one consistent pattern: buying stocks that just got oversold -- Connors RSI and
Money Flow -- beat holding in both halves on daily and weekly candles (no version
reached t 3 in both). The published reason is the short-term reversal effect
(Jegadeesh 1990, Lehmann 1990). These books trade it forward, VIRTUAL money only.

  VERSIONS  daily / weekly x connors, connors+adx, mfi, mfi+adx (rules.RULES)
  WHEN      daily books act once a day, at the first regular-hours pass, on the
            completed daily candles; weekly books on Monday's first pass, on the
            completed weekly candles (ISO weeks)
  TRADES    as #94: entry at the live price (stands in for the next open), exit when
            the state turns 0, 3 x ATR(22) stop of that timeframe, holds through
            weekends, 0.05% a side (signals/paper.py)
PURE.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

SLOW_RULES = ("connors", "mfi")


def weekly(rows: Sequence[tuple]) -> list[tuple]:
    """Daily (date, o, h, l, c, v) -> weekly candles dated by their LAST day."""
    out: list = []
    for d, o, h, lo, c, v in rows:
        k = d.isocalendar()[:2]
        if out and out[-1][0] == k:
            _, (_, o0, h0, l0, _, v0) = out[-1]
            out[-1] = (k, (d, o0, max(h0, h), min(l0, lo), c, v0 + v))
        else:
            out.append((k, (d, o, h, lo, c, v)))
    return [x for _, x in out]


def completed_daily(rows: Sequence[tuple], today: date) -> list[tuple]:
    """Drop today's forming candle."""
    return [r for r in rows if r[0] < today]


def completed_weekly(rows: Sequence[tuple], today: date) -> list[tuple]:
    """Weekly candles of weeks that ended before today's week."""
    this_week = today.isocalendar()[:2]
    return [w for w in weekly(rows) if w[0].isocalendar()[:2] < this_week]
