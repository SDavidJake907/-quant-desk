"""Math signals tests.

Tests that all mathematical indicators produce correct percentages
and sensible explanations for Gemma's decision context.
"""

from __future__ import annotations

import pytest

from scanner.execution.math_signals import (
    MathSignal,
    build_signals,
    expected_value_pct,
    mean_distance_pct,
    position_pct,
    range_position_pct,
    sector_correlation_pct,
    setup_score_pct,
    volatility_percentile_pct,
    volume_pct,
)


# ---- position_pct --------------------------------------------------------

def test_position_pct_positive():
    s = position_pct(105.0, 100.0)
    assert s.value_pct == 5.0
    assert s.confidence == 1.0
    assert "up" in s.explanation


def test_position_pct_negative():
    s = position_pct(95.0, 100.0)
    assert s.value_pct == -5.0
    assert s.confidence == 1.0
    assert "down" in s.explanation


def test_position_pct_zero_entry():
    s = position_pct(100.0, 0.0)
    assert s.value_pct == 0.0
    assert s.confidence == 0.0


# ---- mean_distance_pct ---------------------------------------------------

def test_mean_distance_pct_above():
    s = mean_distance_pct(105.0, 100.0)
    assert s.value_pct == 5.0
    assert "above" in s.explanation


def test_mean_distance_pct_below():
    s = mean_distance_pct(95.0, 100.0)
    assert s.value_pct == -5.0
    assert "below" in s.explanation


def test_mean_distance_pct_zero_sma():
    s = mean_distance_pct(100.0, 0.0)
    assert s.value_pct == 0.0
    assert s.confidence == 0.0


# ---- range_position_pct --------------------------------------------------

def test_range_position_pct_middle():
    s = range_position_pct(102.5, 105.0, 100.0)
    assert s.value_pct == 50.0
    assert "middle" in s.explanation


def test_range_position_pct_top():
    s = range_position_pct(104.0, 105.0, 100.0)
    assert s.value_pct == 80.0
    assert "top" in s.explanation


def test_range_position_pct_bottom():
    s = range_position_pct(101.0, 105.0, 100.0)
    assert s.value_pct == 20.0
    assert "bottom" in s.explanation


def test_range_position_pct_equal_high_low():
    s = range_position_pct(100.0, 100.0, 100.0)
    assert s.value_pct == 50.0
    assert s.confidence == 0.0


# ---- volatility_percentile_pct ------------------------------------------

def test_volatility_percentile_pct_high():
    s = volatility_percentile_pct(5.0, [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5])
    assert s.value_pct == 80.0  # 8 of 10 are below 5.0
    assert "high volatility" in s.explanation


def test_volatility_percentile_pct_low():
    s = volatility_percentile_pct(1.5, [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5])
    assert s.value_pct == 10.0  # 1 of 10 are below 1.5
    assert "low volatility" in s.explanation


def test_volatility_percentile_pct_insufficient():
    s = volatility_percentile_pct(2.5, [1.0, 2.0])
    assert s.value_pct == 50.0
    assert s.confidence == 0.0


# ---- expected_value_pct --------------------------------------------------

def test_expected_value_pct_positive():
    s = expected_value_pct(0.6, 5.0, 0.4, 2.0)
    assert s.value_pct == 2.2  # 0.6*5 - 0.4*2 = 3.0 - 0.8 = 2.2
    assert "positive" in s.explanation


def test_expected_value_pct_negative():
    s = expected_value_pct(0.3, 5.0, 0.7, 4.0)
    assert s.value_pct == -1.3  # 0.3*5 - 0.7*4 = 1.5 - 2.8 = -1.3
    assert "negative" in s.explanation


# ---- sector_correlation_pct ---------------------------------------------

def test_sector_correlation_pct_positive():
    sx = [0.01, 0.02, 0.03, 0.04, 0.05]
    sy = [0.02, 0.04, 0.06, 0.08, 0.10]
    s = sector_correlation_pct(sx, sy)
    assert abs(s.value_pct - 100.0) < 1.0  # perfectly correlated
    assert "highly correlated" in s.explanation


def test_sector_correlation_pct_insufficient():
    s = sector_correlation_pct([0.01], [0.02])
    assert s.value_pct == 0.0
    assert s.confidence == 0.0


# ---- volume_pct ----------------------------------------------------------

def test_volume_pct_above_average():
    s = volume_pct(1500000, 1000000)
    assert s.value_pct == 150.0
    assert "high volume" in s.explanation


def test_volume_pct_below_average():
    s = volume_pct(500000, 1000000)
    assert s.value_pct == 50.0
    assert "low volume" in s.explanation


def test_volume_pct_zero_average():
    s = volume_pct(1000000, 0.0)
    assert s.value_pct == 100.0
    assert s.confidence == 0.0


# ---- setup_score_pct -----------------------------------------------------

def test_setup_score_pct_strong():
    s = setup_score_pct(0.0, 80.0, 50.0, 5.0, 150.0)
    assert s.value_pct > 50
    assert "strong setup" in s.explanation


def test_setup_score_pct_poor():
    s = setup_score_pct(90.0, 50.0, 95.0, -100.0, 10.0)
    assert s.value_pct < -20
    assert "poor setup" in s.explanation


# ---- build_signals -------------------------------------------------------

def test_build_signals_returns_all_signals():
    signals = build_signals(
        current_price=105.0,
        entry_price=100.0,
        sma_20=100.0,
        recent_high=110.0,
        recent_low=95.0,
        current_atr_pct=2.5,
        atr_history=[1.0, 1.5, 2.0, 2.5, 3.0],
        win_probability=0.4,
        avg_win_pct=5.0,
        loss_probability=0.6,
        avg_loss_pct=2.0,
        stock_returns=[0.01, 0.02, -0.01, 0.03],
        portfolio_returns=[0.005, 0.01, -0.005, 0.015],
        current_volume=1500000,
        avg_volume_20d=1000000,
    )
    assert len(signals) == 8
    names = {sig.name for sig in signals}
    expected = {
        "position_pct", "mean_distance_pct", "range_position_pct",
        "volatility_percentile_pct", "expected_value_pct",
        "sector_correlation_pct", "volume_pct", "setup_score_pct",
    }
    assert names == expected

    # All signals should have valid confidence
    for sig in signals:
        assert 0.0 <= sig.confidence <= 1.0
        assert sig.explanation != ""
