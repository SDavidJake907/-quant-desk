"""A bucket with no trading is not a bar.

Yahoo emits a placeholder at 16:00 carrying the closing print: zero volume,
zero range, high equal to low. It appeared on 12.7% of session-days and made a
09:30-16:00 session 14 buckets long when it is 13.

It cost real money in one specific way. The liquidity filter averages
volume x close over the last 20 bars, so a zero dragged the mean down 6.51%;
against a 20M floor, where the median S&P 500 name measured 19.89M, that
pushed 22 genuinely liquid large caps below the line. KEY measured 19,847,701
and actually trades 22,728,781.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from scanner.features import history
from scanner.features.bars import BarsError
from scanner.features.intraday import SESSION_ALIGNED, resample


def _payload(rows):
    """Rows of (epoch, open, high, low, close, volume)."""
    return {"chart": {"result": [{
        "timestamp": [r[0] for r in rows],
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "indicators": {"quote": [{
            "open": [r[1] for r in rows], "high": [r[2] for r in rows],
            "low": [r[3] for r in rows], "close": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
        }]},
    }]}}


#: 2026-09-25 09:30 ET onwards, in epoch seconds.
BASE = int(datetime(2026, 9, 25, 13, 30).timestamp())


def _real(n):
    return [(BASE + i * 1800, 10.0, 11.0, 9.0, 10.5, 1_000_000)
            for i in range(n)]


def test_a_zero_volume_zero_range_bucket_is_dropped(bars_factory=None):
    rows = _real(5) + [(BASE + 5 * 1800, 10.5, 10.5, 10.5, 10.5, 0)]
    bars = resample(_payload(rows), "TEST", minutes=30,
                    alignment=SESSION_ALIGNED)
    assert len(bars) == 5, "the placeholder survived into the series"


def test_a_real_flat_bar_with_volume_is_kept():
    """high == low can happen on a quiet name. Volume is what makes it real,
    so requiring BOTH conditions is what keeps this from over-deleting."""
    rows = _real(5) + [(BASE + 5 * 1800, 10.5, 10.5, 10.5, 10.5, 250_000)]
    bars = resample(_payload(rows), "TEST", minutes=30,
                    alignment=SESSION_ALIGNED)
    assert len(bars) == 6


def test_a_zero_volume_bar_that_moved_is_kept():
    """Belt and braces: only the exact artifact is targeted."""
    rows = _real(5) + [(BASE + 5 * 1800, 10.0, 11.0, 9.0, 10.5, 0)]
    bars = resample(_payload(rows), "TEST", minutes=30,
                    alignment=SESSION_ALIGNED)
    assert len(bars) == 6


def test_a_series_of_only_placeholders_raises_rather_than_returning_one_bar():
    rows = [(BASE + i * 1800, 10.5, 10.5, 10.5, 10.5, 0) for i in range(4)]
    with pytest.raises(BarsError):
        resample(_payload(rows), "TEST", minutes=30, alignment=SESSION_ALIGNED)


def test_the_store_filters_placeholders_on_read():
    """The store is append-only and already holds 2,591 of them, written
    before resample learned to reject them. They are filtered on READ rather
    than deleted, because the store's value is recording what the provider
    actually returned."""
    rows = [[BASE + i * 1800, 10.0, 11.0, 9.0, 10.5, 1_000_000]
            for i in range(4)]
    rows.append([BASE + 4 * 1800, 10.5, 10.5, 10.5, 10.5, 0])
    bars = history.to_bars("TEST", 30, SESSION_ALIGNED, rows)
    assert len(bars) == 4
