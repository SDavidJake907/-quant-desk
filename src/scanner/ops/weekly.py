"""The Friday weekly review (WORKLIST §45). Pure helpers; the script gathers.

The operator reviews every open position on Friday morning and decides what
to hold over the weekend. This states facts in one place:
- % since entry and % over the last five sessions, per position and per book,
  against the S&P 500 over the same five sessions
- how far each position sits above its stop
- a TREND LABEL that DESCRIBES, it does not predict:
    uptrend    price above its 50-day average AND up over the last month
    downtrend  price below its 50-day average AND down over the last month
    mixed      anything else
  (Price vs averages tested as no edge here, §31: the label says where a stock
  stands, which is what a weekend decision needs, not where it goes.)
"""

from __future__ import annotations

from collections.abc import Sequence

UP, DOWN, MIXED = "uptrend", "downtrend", "mixed"


def pct(a: float, b: float) -> float:
    """% change from b to a."""
    return (a / b - 1) * 100 if b else 0.0


def trend_label(closes: Sequence[float]) -> str:
    if len(closes) < 50:
        return MIXED
    last = closes[-1]
    above = last > sum(closes[-50:]) / 50
    month = pct(last, closes[-22]) if len(closes) > 21 else 0.0
    if above and month > 0:
        return UP
    if not above and month < 0:
        return DOWN
    return MIXED


def week_change(closes: Sequence[float], sessions: int = 5) -> float:
    return pct(closes[-1], closes[-1 - sessions]) if len(closes) > sessions else 0.0


def stop_room(price: float, stop: float | None) -> float | None:
    """How far the price can fall before the stop, as % of the price; None
    when there is no stop."""
    return (price - stop) / price * 100 if stop and price else None
