from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from scanner.features.intraday import EXCHANGE_TZ, IntradayBars
from scanner.features.intraday_context import compute, ema, rsi


def _bars(closes: list[float]) -> IntradayBars:
    start = datetime(2026, 1, 5, 9, 30, tzinfo=EXCHANGE_TZ)
    return IntradayBars(
        symbol="TEST", interval_minutes=240, alignment="session",
        times=tuple(start + timedelta(hours=4 * i) for i in range(len(closes))),
        opens=tuple(closes), highs=tuple(c + 1 for c in closes),
        lows=tuple(c - 1 for c in closes), closes=tuple(closes),
        volumes=tuple(1000.0 for _ in closes),
    )


def test_ema_is_none_until_enough_history():
    assert ema((1.0, 2.0), 3) is None


def test_rsi_rises_on_monotonic_closes():
    value = rsi(tuple(float(i) for i in range(1, 30)))
    assert value == pytest.approx(100.0)


def test_context_is_closed_bar_only():
    base = _bars([100 + i for i in range(220)])
    extended = _bars([100 + i for i in range(220)] + [50.0])
    assert compute(base).price == compute(extended.upto(219)).price
    assert compute(base).ema50 == pytest.approx(compute(extended.upto(219)).ema50)


def test_context_exposes_macd_rsi_and_atr():
    context = compute(_bars([100 + i * 0.2 for i in range(220)]))
    assert context.rsi14 is not None
    assert context.macd is not None
    assert context.atr_pct is not None
