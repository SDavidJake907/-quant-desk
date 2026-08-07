"""Volume-profile tests.

The profile is built from an assumption — that each bar's volume spread
evenly across its range — so the tests pin what that assumption does as much
as what the algorithm does. A level derived from an approximation should not
be treated as measured, and the only way to keep that honest is to know
exactly where the approximation bites.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from scanner.features.intraday import EXCHANGE_TZ, IntradayBars
from scanner.features.profile import ProfileError, build


def _bars(rows: list[tuple[float, float, float]]) -> IntradayBars:
    """rows: (high, low, volume)"""
    start = datetime(2026, 1, 5, 9, 30, tzinfo=EXCHANGE_TZ)
    return IntradayBars(
        symbol="TEST", interval_minutes=240, alignment="session",
        times=tuple(start + timedelta(hours=4 * i) for i in range(len(rows))),
        opens=tuple((r[0] + r[1]) / 2 for r in rows),
        highs=tuple(r[0] for r in rows),
        lows=tuple(r[1] for r in rows),
        closes=tuple((r[0] + r[1]) / 2 for r in rows),
        volumes=tuple(r[2] for r in rows),
    )


# ---- the point of control ----------------------------------------------

def test_poc_lands_where_the_volume_concentrated():
    """Most bars sit at 100-101; one outlier ranges far above on thin volume."""
    rows = [(101, 100, 1000)] * 10 + [(120, 102, 50)]
    profile = build(_bars(rows), 0, 10, bins=20)
    assert 100 <= profile.poc_price <= 101.5


def test_poc_is_the_heaviest_bin():
    profile = build(_bars([(101, 100, 1000)] * 5 + [(110, 109, 200)] * 3), 0, 7, bins=20)
    assert profile.bins[profile.poc_index] == max(profile.bins)


def test_volume_is_conserved():
    rows = [(105, 100, 700), (110, 103, 300), (108, 101, 500)]
    profile = build(_bars(rows), 0, 2, bins=25)
    assert profile.total_volume == pytest.approx(1500.0)


# ---- the value area ----------------------------------------------------

def test_value_area_holds_at_least_the_requested_share():
    rows = [(105, 100, 900)] * 8 + [(130, 120, 100)] * 2
    profile = build(_bars(rows), 0, 9, bins=40, value_area=0.70)
    assert profile.value_area_volume >= 0.70 * profile.total_volume


def test_value_area_brackets_the_poc():
    rows = [(105, 100, 900)] * 8 + [(130, 120, 100)] * 2
    profile = build(_bars(rows), 0, 9, bins=40)
    assert profile.value_area_low <= profile.poc_price <= profile.value_area_high


def test_a_wider_value_area_never_shrinks():
    rows = [(105, 100, 900)] * 8 + [(130, 120, 100)] * 2
    narrow = build(_bars(rows), 0, 9, bins=40, value_area=0.50)
    wide = build(_bars(rows), 0, 9, bins=40, value_area=0.95)
    assert wide.value_area_high - wide.value_area_low >= narrow.value_area_high - narrow.value_area_low


def test_full_value_area_covers_the_range():
    rows = [(105, 100, 900), (110, 104, 200), (108, 101, 400)]
    profile = build(_bars(rows), 0, 2, bins=20, value_area=1.0)
    assert profile.value_area_low == pytest.approx(profile.low)
    assert profile.value_area_high == pytest.approx(profile.high)


def test_contains_answers_inside_and_outside():
    rows = [(105, 100, 900)] * 8 + [(130, 120, 100)] * 2
    profile = build(_bars(rows), 0, 9, bins=40)
    assert profile.contains(profile.poc_price)
    assert not profile.contains(profile.high + 1)


# ---- what the uniform-spread assumption costs --------------------------

def test_a_wide_bar_smears_its_volume_and_a_tight_one_concentrates_it():
    """The documented bias: equal volume in a wide bar and a tight bar does
    not weigh equally per bin, so the profile leans toward quiet areas. This
    is why our PoC will never exactly match a tick-data platform's."""
    wide = build(_bars([(120, 100, 1000), (121, 101, 1)]), 0, 1, bins=20)
    tight = build(_bars([(101, 100, 1000), (121, 101, 1)]), 0, 1, bins=20)
    assert max(wide.bins) < max(tight.bins)


def test_a_bar_with_no_range_still_contributes():
    profile = build(_bars([(100, 100, 500), (110, 100, 500)]), 0, 1, bins=10)
    assert profile.total_volume == pytest.approx(1000.0)


# ---- range handling ----------------------------------------------------

def test_only_the_requested_range_is_profiled():
    rows = [(101, 100, 1000)] * 5 + [(200, 190, 9999)] * 5
    profile = build(_bars(rows), 0, 4, bins=20)
    assert profile.high <= 101
    assert profile.total_volume == pytest.approx(5000.0)


def test_range_is_clamped_to_the_series():
    rows = [(101, 100, 100)] * 4
    profile = build(_bars(rows), -5, 99, bins=10)
    assert profile.start_index == 0 and profile.end_index == 3


# ---- failure modes -----------------------------------------------------

def test_a_single_bar_is_not_a_profile():
    with pytest.raises(ProfileError):
        build(_bars([(101, 100, 10)] * 4), 2, 2)


def test_a_flat_range_has_no_extent():
    with pytest.raises(ProfileError):
        build(_bars([(100, 100, 10)] * 4), 0, 3)


def test_zero_volume_is_an_error_not_a_level():
    """A profile of nothing would still report a PoC, and that number would
    look exactly as authoritative as a real one."""
    with pytest.raises(ProfileError):
        build(_bars([(105, 100, 0)] * 4), 0, 3)


def test_bad_parameters_are_rejected():
    bars = _bars([(105, 100, 10)] * 4)
    with pytest.raises(ProfileError):
        build(bars, 0, 3, bins=0)
    with pytest.raises(ProfileError):
        build(bars, 0, 3, value_area=0.0)
    with pytest.raises(ProfileError):
        build(bars, 0, 3, value_area=1.5)
