"""Feature-layer tests.

These functions decide what the screen sees, so an off-by-one here silently
changes every ranking. Synthetic series with known answers.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scanner.features.bars import Bars
from scanner.features.technicals import (
    compute,
    pct_change,
    relative_strength,
    sma,
)


def _bars(closes: list[float], symbol: str = "TEST") -> Bars:
    n = len(closes)
    start = date(2024, 1, 1)
    return Bars(
        symbol=symbol,
        dates=tuple(start + timedelta(days=i) for i in range(n)),
        opens=tuple(closes),
        closes=tuple(closes),
        volumes=tuple(1_000_000.0 for _ in range(n)),
        highs=tuple(c * 1.01 for c in closes),
        lows=tuple(c * 0.99 for c in closes),
    )


def test_sma_uses_most_recent_window():
    assert sma((1.0, 2.0, 3.0, 4.0), 2) == 3.5


def test_sma_returns_none_without_enough_data():
    assert sma((1.0, 2.0), 5) is None


def test_pct_change_handles_zero_base():
    assert pct_change(0.0, 5.0) is None
    assert pct_change(100.0, 110.0) == pytest.approx(10.0)


def test_momentum_excludes_the_most_recent_month():
    """12-1 momentum must ignore the last 21 sessions by construction."""
    closes = [100.0] * 300
    closes[-252] = 100.0   # window start
    closes[-21] = 200.0    # window end -> +100%
    closes[-1] = 999.0     # last month must NOT influence the result
    features = compute(_bars(closes))
    assert features.momentum_12_1 == pytest.approx(100.0)


def test_52w_high_and_distance():
    closes = [100.0] * 250 + [80.0]
    features = compute(_bars(closes))
    assert features.high_52w == 100.0
    assert features.pct_from_52w_high == pytest.approx(-20.0)


def test_uptrend_produces_stacked_moving_averages():
    closes = [float(i) for i in range(1, 301)]
    f = compute(_bars(closes))
    assert f.sma50 > f.sma150 > f.sma200
    assert f.sma200_slope_pct is not None and f.sma200_slope_pct > 0


def test_downtrend_inverts_the_stack():
    closes = [float(i) for i in range(300, 0, -1)]
    f = compute(_bars(closes))
    assert f.sma50 < f.sma150 < f.sma200
    assert f.sma200_slope_pct is not None and f.sma200_slope_pct < 0


def test_atr_pct_is_positive_for_moving_prices():
    f = compute(_bars([100.0 + (i % 5) for i in range(60)]))
    assert f.atr_pct is not None and f.atr_pct > 0


def test_relative_strength_is_difference_in_momentum():
    strong = compute(_bars([100.0] * 252 + [100.0] * 30, "STRONG"))
    weak = compute(_bars([100.0] * 252 + [100.0] * 30, "WEAK"))
    assert relative_strength(strong, weak) == pytest.approx(0.0)


def test_relative_strength_none_when_history_short():
    short = compute(_bars([100.0, 101.0, 102.0]))
    long = compute(_bars([100.0] * 300))
    assert relative_strength(short, long) is None
