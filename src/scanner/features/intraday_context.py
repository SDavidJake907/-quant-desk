"""Closed-bar context for intraday setup research.

These indicators are deliberately descriptive gates, not standalone signals.
They are calculated only from the bars supplied by the caller, so a backtest
can pass ``bars.upto(trigger_index)`` and preserve point-in-time behavior.
"""

from __future__ import annotations

import statistics

from scanner.features.intraday import IntradayBars
from scanner.models import Frozen


def ema(values: tuple[float, ...], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    value = sum(values[:window]) / window
    alpha = 2.0 / (window + 1.0)
    for current in values[window:]:
        value = alpha * current + (1.0 - alpha) * value
    return value


def rsi(values: tuple[float, ...], window: int = 14) -> float | None:
    """Wilder RSI using only closed prices."""
    if window <= 0 or len(values) < window + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:window]) / window
    average_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        average_gain = ((average_gain * (window - 1)) + gain) / window
        average_loss = ((average_loss * (window - 1)) + loss) / window
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def atr_pct(bars: IntradayBars, window: int = 14) -> float | None:
    if window <= 0 or len(bars) < window + 1:
        return None
    true_ranges = []
    for i in range(len(bars) - window, len(bars)):
        prior_close = bars.closes[i - 1]
        true_ranges.append(max(
            bars.highs[i] - bars.lows[i],
            abs(bars.highs[i] - prior_close),
            abs(bars.lows[i] - prior_close),
        ))
    close = bars.closes[-1]
    return (sum(true_ranges) / window) / close * 100.0 if close else None


class IntradayContext(Frozen):
    price: float
    ema9: float | None
    ema20: float | None
    ema21: float | None
    ema50: float | None
    ema200: float | None
    ema50_slope_pct: float | None
    rsi14: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    atr_pct: float | None
    volume_ratio: float | None
    range_tightness: float | None

    @property
    def bullish_alignment(self) -> bool:
        values = (self.ema21, self.ema50, self.ema200)
        return all(value is not None for value in values) and (
            self.price > self.ema21 > self.ema50 > self.ema200
        ) and (self.ema50_slope_pct is None or self.ema50_slope_pct > 0)

    @property
    def preferred_pullback(self) -> bool:
        return self.rsi14 is not None and 40.0 <= self.rsi14 <= 65.0

    @property
    def fast_stack(self) -> bool:
        """Price > 9 EMA > 21 EMA > 50 EMA — the short-term alignment the
        infographics call a bullish setup. Stricter and faster-reacting than
        `bullish_alignment`, which anchors on the 200."""
        values = (self.ema9, self.ema21, self.ema50)
        return all(v is not None for v in values) and (
            self.price > self.ema9 > self.ema21 > self.ema50
        )

    @property
    def above_200(self) -> bool:
        """"Trend first": above the 200 EMA, look for longs only."""
        return self.ema200 is not None and self.price > self.ema200

    @property
    def strong_momentum(self) -> bool:
        """RSI above 60 — the breakout-continuation reading, as opposed to
        `preferred_pullback`, which wants 40-65. THE TWO CONFLICT by design:
        one describes a stock already running, the other one resting. They
        overlap only in 60-65, so requiring both is nearly requiring that."""
        return self.rsi14 is not None and self.rsi14 > 60.0

    @property
    def macd_above_zero(self) -> bool:
        """Crossover AND above the zero line — the infographic's "best entry".
        Above zero means the fast EMA leads the slow one outright, not merely
        turning up inside a downtrend."""
        return self.macd_confirmed and self.macd is not None and self.macd > 0.0

    @property
    def coiled(self) -> bool:
        """Tight consolidation: recent range small relative to ATR, and volume
        drying up. Supply exhausted rather than merely paused.

        Note the daily `coiled` screen — the same idea on daily bars — LOST to
        a random control in this repo. That is not proof it fails intraday,
        but it is the reason this stays a flag rather than a default."""
        return (
            self.range_tightness is not None
            and self.range_tightness < 6.0
            and self.volume_ratio is not None
            and self.volume_ratio < 1.0
        )

    @property
    def macd_confirmed(self) -> bool:
        return (
            self.macd is not None
            and self.macd_signal is not None
            and self.macd_histogram is not None
            and self.macd > self.macd_signal
            and self.macd_histogram > 0
        )


def compute(bars: IntradayBars) -> IntradayContext:
    closes = bars.closes
    ema50 = ema(closes, 50)
    prior_ema50 = ema(closes[:-1], 50) if len(closes) >= 51 else None
    slope = None
    if ema50 is not None and prior_ema50:
        slope = (ema50 - prior_ema50) / prior_ema50 * 100.0

    fast = ema(closes, 12)
    slow = ema(closes, 26)
    macd_value = fast - slow if fast is not None and slow is not None else None
    macd_signal = None
    histogram = None
    if len(closes) >= 35:
        macd_series = []
        for end in range(26, len(closes) + 1):
            part = closes[:end]
            short = ema(part, 12)
            long = ema(part, 26)
            if short is not None and long is not None:
                macd_series.append(short - long)
        macd_signal = ema(tuple(macd_series), 9)
        if macd_value is not None and macd_signal is not None:
            histogram = macd_value - macd_signal

    tightness = None
    atr = atr_pct(bars)
    if len(closes) >= 20 and atr:
        recent_high, recent_low = max(closes[-20:]), min(closes[-20:])
        if recent_low:
            tightness = ((recent_high - recent_low) / recent_low * 100.0) / atr

    volume_ratio = None
    if len(bars.volumes) >= 21:
        baseline = statistics.median(bars.volumes[-21:-1])
        volume_ratio = bars.volumes[-1] / baseline if baseline else None

    return IntradayContext(
        price=closes[-1],
        ema9=ema(closes, 9),
        ema20=ema(closes, 20),
        ema21=ema(closes, 21),
        ema50=ema50,
        ema200=ema(closes, 200),
        ema50_slope_pct=slope,
        rsi14=rsi(closes),
        macd=macd_value,
        macd_signal=macd_signal,
        macd_histogram=histogram,
        atr_pct=atr,
        volume_ratio=volume_ratio,
        range_tightness=tightness,
    )


def passes(
    bars: IntradayBars,
    *,
    require_trend: bool = False,
    require_rsi: bool = False,
    require_macd: bool = False,
    require_fast_stack: bool = False,
    require_above_200: bool = False,
    require_strong_rsi: bool = False,
    require_macd_zero: bool = False,
    require_coiled: bool = False,
) -> bool:
    context = compute(bars)
    return (
        (not require_trend or context.bullish_alignment)
        and (not require_rsi or context.preferred_pullback)
        and (not require_macd or context.macd_confirmed)
        and (not require_fast_stack or context.fast_stack)
        and (not require_above_200 or context.above_200)
        and (not require_strong_rsi or context.strong_momentum)
        and (not require_macd_zero or context.macd_above_zero)
        and (not require_coiled or context.coiled)
    )


def rsi_series(values: tuple[float, ...], window: int = 14
               ) -> tuple[float | None, ...]:
    """Wilder RSI at every index, aligned to `values`.

    `rsi()` returns one number for the whole series. Divergence needs the value
    at two historical pivots, and recomputing `rsi()` over a slice per pivot is
    both O(n^2) and subtly wrong at the seam — the Wilder average is recursive,
    so a fresh start on a slice is not the same number a running average would
    have produced there.

    Indices before the window can not be computed and are None rather than
    zero, so a caller cannot silently compare a real RSI against a filler.
    """
    if window <= 0 or len(values) < window + 1:
        return tuple(None for _ in values)

    out: list[float | None] = [None] * len(values)
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]

    average_gain = sum(gains[:window]) / window
    average_loss = sum(losses[:window]) / window

    def _value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - (100.0 / (1.0 + gain / loss))

    out[window] = _value(average_gain, average_loss)
    for offset, (gain, loss) in enumerate(zip(gains[window:], losses[window:])):
        average_gain = ((average_gain * (window - 1)) + gain) / window
        average_loss = ((average_loss * (window - 1)) + loss) / window
        out[window + 1 + offset] = _value(average_gain, average_loss)
    return tuple(out)


def obv_series(closes: tuple[float, ...], volumes: tuple[float, ...]
               ) -> tuple[float, ...]:
    """On-balance volume at every index.

    Volume is added when the close rises and subtracted when it falls, so the
    line rises while buyers are lifting offers even if price goes nowhere.
    That is the whole claim behind "accumulation": distribution and
    accumulation look identical on a price chart and different on this one.

    An unchanged close contributes nothing, which is the standard convention.
    """
    if not closes or len(closes) != len(volumes):
        return ()
    out = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            out.append(out[-1] - volumes[i])
        else:
            out.append(out[-1])
    return tuple(out)
