"""Equal highs and equal lows: where resting stops actually cluster.

`swings` deliberately refuses ties -- a bar that merely matches its neighbours
is not a pivot, because admitting ties turns flat congestion into a forest of
imaginary structure. That is right for a pivot detector and it has a side
effect: the formation SMC material calls the primary liquidity pool, a run of
roughly EQUAL lows, is invisible to everything downstream.

These levels do not have to tie to the tick. Two swing lows a few basis points
apart hold the same stops, so "equal" is a tolerance band, and the width of
that band is a parameter to be measured rather than assumed.
"""

from __future__ import annotations

import pytest

from scanner.features.structure import HIGH, LOW, Swing, equal_levels


def _lows(*prices: float) -> tuple[Swing, ...]:
    return tuple(Swing(index=i * 10, price=p, kind=LOW) for i, p in enumerate(prices))


class TestCounting:
    def test_a_lone_level_counts_itself(self):
        assert equal_levels(_lows(100.0), 100.0, LOW, tolerance_pct=0.1) == 1

    def test_two_near_identical_lows_are_one_pool(self):
        assert equal_levels(_lows(100.0, 100.05), 100.0, LOW, tolerance_pct=0.1) == 2

    def test_a_level_outside_the_band_is_a_different_pool(self):
        assert equal_levels(_lows(100.0, 101.0), 100.0, LOW, tolerance_pct=0.1) == 1

    def test_the_band_is_a_percentage_not_an_absolute(self):
        """A 0.1% band is 10 cents on a $100 stock and $5 on a $5,000 one.
        An absolute tolerance would call every mega-cap level unequal."""
        assert equal_levels(_lows(5000.0, 5004.0), 5000.0, LOW,
                            tolerance_pct=0.1) == 2
        assert equal_levels(_lows(10.0, 10.02), 10.0, LOW, tolerance_pct=0.1) == 1

    def test_three_stacked_lows_count_three(self):
        assert equal_levels(_lows(50.0, 50.01, 49.99), 50.0, LOW,
                            tolerance_pct=0.1) == 3


class TestKindIsRespected:
    def test_highs_do_not_count_toward_a_low_pool(self):
        points = _lows(100.0) + (Swing(index=99, price=100.0, kind=HIGH),)
        assert equal_levels(points, 100.0, LOW, tolerance_pct=0.1) == 1

    def test_a_high_pool_counts_only_highs(self):
        points = (Swing(index=0, price=200.0, kind=HIGH),
                  Swing(index=10, price=200.1, kind=HIGH),
                  Swing(index=20, price=200.0, kind=LOW))
        assert equal_levels(points, 200.0, HIGH, tolerance_pct=0.1) == 2


class TestRefusals:
    def test_no_points_is_zero(self):
        assert equal_levels((), 100.0, LOW, tolerance_pct=0.1) == 0

    def test_a_non_positive_reference_is_zero(self):
        """Percentage bands are undefined at or below zero."""
        assert equal_levels(_lows(0.0), 0.0, LOW, tolerance_pct=0.1) == 0

    def test_a_negative_tolerance_is_refused(self):
        with pytest.raises(ValueError):
            equal_levels(_lows(100.0), 100.0, LOW, tolerance_pct=-0.1)
