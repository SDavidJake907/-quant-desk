"""Extra lines in each stock's block for Gemma (operator 2026-10-19, "add them now").

  analysts      EPS estimate change and how many analysts raised/cut it (30 days)
  fundamentals  forward P/E, revenue and earnings growth, margins
  chart         price vs its own 50/200-day averages, relative volume, ATR rank

The first two are a kind of information she has never had (what companies earn,
what analysts expect). The chart line is context only: moving-average trend,
52-week and volume measures tested as no edge on this desk (TRIALS #28-#39),
and her rules say so. Oscillators (RSI, MACD) are deliberately NOT shown.

PURE: numbers in, one line of text out (or "" when there is nothing to say).
"""

from __future__ import annotations

from collections.abc import Sequence

from scanner.sources.yahoo_fundamentals import Fundamentals

RVOL_WINDOW = 20
ATR_WINDOW = 14
ATR_RANK_DAYS = 252


def _pct(x: float | None, *, scale: float = 100.0) -> str | None:
    return None if x is None else f"{x * scale:+.0f}%"


def analysts_line(f: Fundamentals | None) -> str:
    if f is None:
        return ""
    parts = []
    if f.eps_now and f.eps_90d_ago:
        parts.append(f"EPS est {(f.eps_now / f.eps_90d_ago - 1) * 100:+.1f}% (90d)")
    elif f.eps_now and f.eps_30d_ago:
        parts.append(f"EPS est {(f.eps_now / f.eps_30d_ago - 1) * 100:+.1f}% (30d)")
    if f.up_30d is not None or f.down_30d is not None:
        parts.append(f"{f.up_30d or 0} up/{f.down_30d or 0} down (30d)")
    return "analysts: " + ", ".join(parts) if parts else ""


def fundamentals_line(f: Fundamentals | None) -> str:
    if f is None:
        return ""
    parts = []
    if f.forward_pe is not None and f.forward_pe > 0:
        parts.append(f"fwd P/E {f.forward_pe:.1f}")
    for label, value in (("rev", f.revenue_growth), ("earn", f.earnings_growth),
                         ("gross", f.gross_margin), ("op", f.operating_margin)):
        text = _pct(value)
        if text is not None:
            parts.append(f"{label} {text.lstrip('+') if label in ('gross', 'op') else text}")
    return "fundamentals: " + ", ".join(parts) if parts else ""


def rvol(volumes: Sequence[float], window: int = RVOL_WINDOW) -> float | None:
    """The last session's volume over the average of the `window` sessions before it."""
    if len(volumes) < window + 1:
        return None
    base = sum(volumes[-window - 1:-1]) / window
    return volumes[-1] / base if base else None


def atr_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               window: int = ATR_WINDOW) -> list[float]:
    """Daily ATR as % of close, one value per day once `window` true ranges exist."""
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    return [sum(trs[i - window:i]) / window / closes[i] * 100
            for i in range(window, len(trs) + 1) if closes[i]]


def atr_rank(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
             days: int = ATR_RANK_DAYS) -> float | None:
    """Percentile of today's ATR% among the last `days` (0 = calmest, 100 = wildest)."""
    series = atr_series(highs, lows, closes)
    if len(series) < 60:
        return None
    hist = series[-days:]
    return sum(v <= hist[-1] for v in hist) / len(hist) * 100


def chart_line(price: float, sma50: float | None, sma200: float | None,
               volumes: Sequence[float], highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float]) -> str:
    parts = []
    for label, avg in (("50-day", sma50), ("200-day", sma200)):
        if avg:
            parts.append(f"vs {label[:-4]}d {(price / avg - 1) * 100:+.1f}%")
    r = rvol(volumes)
    if r is not None:
        parts.append(f"vol {r:.1f}x")
    rank = atr_rank(highs, lows, closes)
    if rank is not None:
        parts.append(f"ATR rank {rank:.0f}")
    return "chart: " + ", ".join(parts) if parts else ""
