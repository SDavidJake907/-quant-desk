"""Deterministic technical features.

Pure functions over price series. No model, no network, no randomness — the
same bars always produce the same numbers, which is what makes any of this
backtestable. This is the layer where real signal lives, if it lives anywhere.
"""

from __future__ import annotations

from scanner.features.bars import Bars
from scanner.models import Frozen

TRADING_DAYS_YEAR = 252
TRADING_DAYS_MONTH = 21


def sma(values: tuple[float, ...], window: int) -> float | None:
    """Simple moving average of the most recent `window` values."""
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def pct_change(start: float, end: float) -> float | None:
    if start == 0:
        return None
    return ((end - start) / start) * 100.0


def average_true_range_pct(bars: Bars, window: int = 14) -> float | None:
    """ATR as a percentage of price — comparable across different price levels."""
    if len(bars) < window + 1:
        return None

    true_ranges: list[float] = []
    for i in range(len(bars) - window, len(bars)):
        prior_close = bars.closes[i - 1]
        true_ranges.append(
            max(
                bars.highs[i] - bars.lows[i],
                abs(bars.highs[i] - prior_close),
                abs(bars.lows[i] - prior_close),
            )
        )
    atr = sum(true_ranges) / len(true_ranges)
    last = bars.closes[-1]
    return (atr / last) * 100.0 if last else None


class Features(Frozen):
    """Everything the screens read. All derived, all reproducible."""

    symbol: str
    price: float
    sma50: float | None
    sma150: float | None
    sma200: float | None
    sma200_slope_pct: float | None
    high_52w: float
    low_52w: float
    pct_from_52w_high: float
    pct_above_52w_low: float
    momentum_12_1: float | None
    atr_pct: float | None
    volume_ratio: float | None
    range_tightness: float | None
    bars_available: int


def compute(bars: Bars) -> Features:
    """Derive all features from one symbol's bars."""
    closes = bars.closes
    price = closes[-1]

    window_52w = closes[-TRADING_DAYS_YEAR:] if len(closes) >= TRADING_DAYS_YEAR else closes
    high_52w, low_52w = max(window_52w), min(window_52w)

    sma200 = sma(closes, 200)
    # Slope: where the 200-day sits now versus a month ago. Positive means the
    # long-term trend is still building.
    sma200_prior = sma(closes[:-TRADING_DAYS_MONTH], 200) if len(closes) > 200 + TRADING_DAYS_MONTH else None
    slope = (
        pct_change(sma200_prior, sma200)
        if sma200 is not None and sma200_prior is not None
        else None
    )

    # 12-1 momentum: 12-month return excluding the most recent month. Skipping
    # the last month is deliberate — short-term reversal works against you.
    momentum = None
    if len(closes) > TRADING_DAYS_YEAR:
        start = closes[-TRADING_DAYS_YEAR]
        end = closes[-TRADING_DAYS_MONTH]
        momentum = pct_change(start, end)

    # Volume: recent 50-day average against the 200-day baseline.
    volume_ratio = None
    if len(bars.volumes) >= 200:
        recent = sum(bars.volumes[-50:]) / 50
        baseline = sum(bars.volumes[-200:]) / 200
        volume_ratio = recent / baseline if baseline else None

    # Tightness: last 20 sessions' range relative to ATR. Low means the stock
    # is coiling rather than swinging.
    tightness = None
    atr_pct = average_true_range_pct(bars)
    if len(closes) >= 20 and atr_pct:
        recent_high, recent_low = max(closes[-20:]), min(closes[-20:])
        span_pct = pct_change(recent_low, recent_high)
        if span_pct is not None:
            tightness = span_pct / atr_pct

    return Features(
        symbol=bars.symbol,
        price=price,
        sma50=sma(closes, 50),
        sma150=sma(closes, 150),
        sma200=sma200,
        sma200_slope_pct=slope,
        high_52w=high_52w,
        low_52w=low_52w,
        pct_from_52w_high=pct_change(high_52w, price) or 0.0,
        pct_above_52w_low=pct_change(low_52w, price) or 0.0,
        momentum_12_1=momentum,
        atr_pct=atr_pct,
        volume_ratio=volume_ratio,
        range_tightness=tightness,
        bars_available=len(bars),
    )


def relative_strength(stock: Features, benchmark: Features) -> float | None:
    """Stock's 12-1 momentum minus the benchmark's.

    Positive means it outpaced the index over the same window. This is the
    single most useful cross-sectional number in the whole feature set.
    """
    if stock.momentum_12_1 is None or benchmark.momentum_12_1 is None:
        return None
    return stock.momentum_12_1 - benchmark.momentum_12_1
