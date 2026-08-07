"""Swing-structure tests.

The pivot is the atom of every price-structure pattern, so a wrong pivot makes
every sweep, CHoCH and break-of-structure above it wrong too — silently, and
in a way that looks convincing on a chart.

The test that matters most is the confirmation-lag one. On a finished chart
every pivot is obvious; live, each arrives k bars late. A detector that
forgets this reports patterns nobody could have traded.
"""

from __future__ import annotations

import pytest

from scanner.features.structure import (
    HIGH,
    LOW,
    Swing,
    broke_above,
    known_at,
    last_swing,
    swing_highs,
    swing_lows,
    swings,
    took_out,
)

#          idx: 0  1  2  3  4  5  6  7  8
PEAK_HIGHS = [1, 2, 3, 9, 3, 2, 1, 2, 1]
PEAK_LOWS = [9, 8, 7, 6, 7, 1, 7, 8, 9]


# ---- finding pivots ----------------------------------------------------

def test_finds_an_obvious_peak():
    assert swing_highs(PEAK_HIGHS, k=2) == (3,)


def test_finds_an_obvious_trough():
    assert swing_lows(PEAK_LOWS, k=2) == (5,)


def test_k_changes_which_pivots_exist():
    """There is no true swing size, so the answer moves with k. A pattern
    that only appears at one value of k has been fitted, not found."""
    highs = [1, 5, 2, 6, 2, 5, 1]
    assert swing_highs(highs, k=1) == (1, 3, 5)
    assert swing_highs(highs, k=2) == (3,)


def test_ties_are_not_pivots():
    """Flat congestion would otherwise sprout imaginary structure."""
    assert swing_highs([1, 3, 3, 3, 1], k=1) == ()
    assert swing_lows([5, 2, 2, 2, 5], k=1) == ()


def test_edges_cannot_be_pivots():
    """A bar without k neighbours on both sides is unconfirmable, and the
    first/last bars of any series are exactly that."""
    rising = [1, 2, 3, 4, 5]
    assert 4 not in swing_highs(rising, k=2)
    assert 0 not in swing_lows(rising, k=2)


def test_series_too_short_yields_nothing():
    assert swing_highs([1, 2, 3], k=2) == ()
    assert swing_lows([], k=2) == ()


def test_k_below_one_is_rejected_rather_than_guessed_at():
    assert swing_highs(PEAK_HIGHS, k=0) == ()


# ---- combined view -----------------------------------------------------

def test_swings_are_ordered_oldest_first():
    points = swings(PEAK_HIGHS, PEAK_LOWS, k=2)
    assert [s.index for s in points] == sorted(s.index for s in points)
    assert {s.kind for s in points} == {HIGH, LOW}


def test_an_outside_bar_can_be_both():
    """One bar engulfing its neighbours both ways is a high and a low. Both
    are reported; what that means is the pattern's problem, not ours."""
    highs = [2, 2, 9, 2, 2]
    lows = [8, 8, 1, 8, 8]
    kinds = {s.kind for s in swings(highs, lows, k=2) if s.index == 2}
    assert kinds == {HIGH, LOW}


def test_last_swing_picks_the_most_recent_of_a_kind():
    points = (
        Swing(index=1, price=10.0, kind=HIGH),
        Swing(index=4, price=5.0, kind=LOW),
        Swing(index=7, price=12.0, kind=HIGH),
    )
    assert last_swing(points, HIGH).index == 7
    assert last_swing(points, LOW).index == 4
    assert last_swing((), HIGH) is None


# ---- the confirmation lag ----------------------------------------------

def test_a_pivot_is_not_known_on_the_bar_it_happens():
    """The whole point. Bar 3 is the peak, but you cannot say so until bar 5."""
    points = swings(PEAK_HIGHS, PEAK_LOWS, k=2)
    peak = next(s for s in points if s.kind == HIGH)
    assert peak.index == 3
    assert peak.confirmed_at(k=2) == 5
    assert known_at(points, index=3, k=2) == ()
    assert known_at(points, index=4, k=2) == ()
    assert peak in known_at(points, index=5, k=2)


def test_known_at_lag_scales_with_k():
    points = swings(PEAK_HIGHS, PEAK_LOWS, k=3)
    if points:
        assert all(s.confirmed_at(3) == s.index + 3 for s in points)


def test_known_at_never_returns_the_future():
    points = swings(PEAK_HIGHS, PEAK_LOWS, k=2)
    for bar in range(len(PEAK_HIGHS)):
        assert all(s.index <= bar for s in known_at(points, bar, k=2))


# ---- sweeps and breaks -------------------------------------------------

def test_a_sweep_requires_trading_through_the_level():
    prior_low = Swing(index=3, price=100.0, kind=LOW)
    assert took_out(99.99, prior_low)
    assert not took_out(100.0, prior_low)   # touching is not taking out
    assert not took_out(100.5, prior_low)


def test_a_break_requires_trading_through_the_high():
    prior_high = Swing(index=3, price=100.0, kind=HIGH)
    assert broke_above(100.01, prior_high)
    assert not broke_above(100.0, prior_high)


def test_sweep_and_break_ignore_the_wrong_kind_of_pivot():
    """Comparing a low against a swing HIGH is a category error, and silently
    returning True would put a phantom sweep in every pattern."""
    a_high = Swing(index=1, price=100.0, kind=HIGH)
    a_low = Swing(index=1, price=100.0, kind=LOW)
    assert not took_out(50.0, a_high)
    assert not broke_above(150.0, a_low)


def test_missing_pivot_is_not_a_signal():
    assert not took_out(1.0, None)
    assert not broke_above(1e9, None)
