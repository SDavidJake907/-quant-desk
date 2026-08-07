"""Intraday resampling tests.

A 4-hour bar for a stock is a construction, not a fact: the session is 6.5
hours and four does not divide it. These tests pin which construction we
build, because a bar boundary in a different place produces a different high,
and a different high produces a different swing — which is the whole input to
every structure pattern downstream.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from scanner.features.bars import BarsError, _cache_path
from scanner.features.intraday import (
    CONTINUOUS,
    EXCHANGE_TZ,
    SESSION_ALIGNED,
    bucket_start,
    resample,
)


def _et(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=EXCHANGE_TZ)


def _payload(rows: list[tuple[datetime, float, float, float, float]]) -> dict:
    """rows: (time, open, high, low, close)"""
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [int(r[0].timestamp()) for r in rows],
                    "indicators": {
                        "quote": [{
                            "open": [r[1] for r in rows],
                            "high": [r[2] for r in rows],
                            "low": [r[3] for r in rows],
                            "close": [r[4] for r in rows],
                            "volume": [100 for _ in rows],
                        }]
                    },
                }
            ]
        }
    }


# A full session: 09:30 through 15:30, seven hourly bars.
SESSION = [
    (_et(2026, 8, 5, 9, 30), 100.0, 105.0, 99.0, 104.0),
    (_et(2026, 8, 5, 10, 30), 104.0, 108.0, 103.0, 107.0),
    (_et(2026, 8, 5, 11, 30), 107.0, 110.0, 106.0, 109.0),
    (_et(2026, 8, 5, 12, 30), 109.0, 112.0, 101.0, 102.0),
    (_et(2026, 8, 5, 13, 30), 102.0, 115.0, 102.0, 114.0),
    (_et(2026, 8, 5, 14, 30), 114.0, 116.0, 113.0, 115.0),
    (_et(2026, 8, 5, 15, 30), 115.0, 118.0, 90.0, 95.0),
]


# ---- bucket boundaries -------------------------------------------------

def test_session_alignment_splits_the_day_at_the_four_hour_mark():
    assert bucket_start(_et(2026, 8, 5, 9, 30), 240, SESSION_ALIGNED) == _et(2026, 8, 5, 9, 30)
    assert bucket_start(_et(2026, 8, 5, 12, 30), 240, SESSION_ALIGNED) == _et(2026, 8, 5, 9, 30)
    assert bucket_start(_et(2026, 8, 5, 13, 30), 240, SESSION_ALIGNED) == _et(2026, 8, 5, 13, 30)
    assert bucket_start(_et(2026, 8, 5, 15, 30), 240, SESSION_ALIGNED) == _et(2026, 8, 5, 13, 30)


def test_continuous_alignment_measures_from_midnight():
    """What most crypto-native platforms draw, and it is a different bar."""
    assert bucket_start(_et(2026, 8, 5, 9, 30), 240, CONTINUOUS) == _et(2026, 8, 5, 8)
    assert bucket_start(_et(2026, 8, 5, 13, 30), 240, CONTINUOUS) == _et(2026, 8, 5, 12)


def test_the_two_alignments_disagree_and_that_is_the_point():
    """Same instant, different bar. Match the chart you intend to agree with."""
    moment = _et(2026, 8, 5, 12, 30)
    assert bucket_start(moment, 240, SESSION_ALIGNED) != bucket_start(moment, 240, CONTINUOUS)


def test_premarket_prints_join_the_session_they_precede():
    assert bucket_start(_et(2026, 8, 5, 7), 240, SESSION_ALIGNED) == _et(2026, 8, 5, 9, 30)


# ---- aggregation -------------------------------------------------------

def test_a_session_folds_into_one_full_bar_and_a_stub():
    bars = resample(_payload(SESSION), "TEST", minutes=240)
    assert len(bars) == 2
    assert bars.times[0] == _et(2026, 8, 5, 9, 30)
    assert bars.times[1] == _et(2026, 8, 5, 13, 30)


def test_open_is_first_close_is_last_high_and_low_are_extremes():
    bars = resample(_payload(SESSION), "TEST", minutes=240)
    assert bars.opens[0] == 100.0     # 09:30 open
    assert bars.closes[0] == 102.0    # 12:30 close
    assert bars.highs[0] == 112.0     # max across the four hours
    assert bars.lows[0] == 99.0       # min across the four hours
    assert bars.highs[1] == 118.0
    assert bars.lows[1] == 90.0


def test_volume_accumulates_across_the_bucket():
    bars = resample(_payload(SESSION), "TEST", minutes=240)
    assert bars.volumes[0] == 400.0   # four hourly bars
    assert bars.volumes[1] == 300.0   # the 2.5-hour stub


def test_interval_changes_the_bar_count():
    two_hour = resample(_payload(SESSION), "TEST", minutes=120)
    four_hour = resample(_payload(SESSION), "TEST", minutes=240)
    assert len(two_hour) > len(four_hour)


def test_missing_prices_are_skipped_not_invented():
    """An interpolated price is a fabricated high, and a fabricated high is a
    fabricated swing."""
    payload = _payload(SESSION)
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
    bars = resample(payload, "TEST", minutes=240)
    assert bars.highs[0] == 112.0  # the 108.0 hour dropped out entirely
    assert bars.volumes[0] == 300.0


def test_bars_stay_in_chronological_order():
    bars = resample(_payload(SESSION), "TEST", minutes=240)
    assert list(bars.times) == sorted(bars.times)


# ---- failure modes -----------------------------------------------------

def test_empty_result_is_an_error():
    with pytest.raises(BarsError):
        resample({"chart": {"result": []}}, "TEST")


def test_a_single_bucket_is_not_a_series():
    with pytest.raises(BarsError):
        resample(_payload(SESSION[:2]), "TEST", minutes=240)


def test_non_positive_interval_is_rejected():
    with pytest.raises(BarsError):
        resample(_payload(SESSION), "TEST", minutes=0)


# ---- point-in-time -----------------------------------------------------

def test_upto_truncates_and_cannot_see_later_bars():
    bars = resample(_payload(SESSION), "TEST", minutes=120)
    early = bars.upto(1)
    assert len(early) == 2
    assert early.closes == bars.closes[:2]
    assert early.times[-1] == bars.times[1]


# ---- the cache key -----------------------------------------------------

def test_interval_is_part_of_the_cache_key():
    """Without it, asking for 60-minute bars after a daily fetch returns daily
    bars, and a 4-hour screen reads them as intraday structure in silence —
    the same trap the range key already exists to prevent."""
    daily = _cache_path("MU", "60d", "1d")
    hourly = _cache_path("MU", "60d", "60m")
    assert daily != hourly
    assert "1d" in daily.name and "60m" in hourly.name


# ---- session slots -----------------------------------------------------

def test_session_slots_split_the_day():
    """The open is separated because it is structurally the noisiest part of
    the session — overnight imbalance and the opening auction resolve there,
    so a 'sweep' during it is often just the opening range forming."""
    from scanner.features.intraday import session_slot

    assert session_slot(_et(2026, 8, 6, 9, 30)) == "open"
    assert session_slot(_et(2026, 8, 6, 10, 29)) == "open"
    assert session_slot(_et(2026, 8, 6, 10, 30)) == "morning"
    assert session_slot(_et(2026, 8, 6, 11, 59)) == "morning"
    assert session_slot(_et(2026, 8, 6, 12, 0)) == "midday"
    assert session_slot(_et(2026, 8, 6, 13, 59)) == "midday"
    assert session_slot(_et(2026, 8, 6, 14, 0)) == "close"
    assert session_slot(_et(2026, 8, 6, 15, 59)) == "close"


def test_session_slot_uses_exchange_time_not_local():
    """A slot computed in UTC would put the New York open in the wrong bucket
    for most of the world."""
    from datetime import timezone as tz

    from scanner.features.intraday import session_slot

    assert session_slot(datetime(2026, 8, 6, 13, 45, tzinfo=tz.utc)) == "open"
