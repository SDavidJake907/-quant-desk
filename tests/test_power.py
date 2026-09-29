"""A null must say what it could have seen.

Twenty-five tests reported null here without once stating their resolution
floor, so every one was read as "no effect" when the honest reading was "no
effect large enough for this sample". These pin the distinction.
"""

from __future__ import annotations

from scanner.backtest.power import (
    MEASURED_SD,
    minimum_detectable,
    needed_per_band,
    verdict,
)


def test_the_floor_falls_as_the_sample_grows():
    big = minimum_detectable(5000, 5000)
    small = minimum_detectable(100, 100)
    assert small > big
    # the measured reality: our holdout cannot resolve a realistic edge
    assert minimum_detectable(144, 144) > 0.5, "holdout floor should be ~0.6R"
    assert minimum_detectable(430, 430) < 0.4, "full sample floor should be ~0.35R"


def test_a_realistic_edge_needs_thousands_of_trades():
    """0.05R-0.2R is the plausible range. The repo has 861 under live rules."""
    assert needed_per_band(0.20) > 600
    assert needed_per_band(0.05) > 10_000


def test_underpowered_is_reported_as_underpowered_not_as_null():
    """The failure this module exists to prevent."""
    said = verdict(observed=0.08, n_a=144, n_b=144, p=0.60)
    assert "UNDERPOWERED" in said
    assert "not null" in said
    assert "Absence of evidence" in said


def test_a_gap_above_the_floor_is_a_genuine_null():
    said = verdict(observed=0.90, n_a=144, n_b=144, p=0.60)
    assert "genuine null" in said
    assert "UNDERPOWERED" not in said


def test_significance_is_reported_with_the_floor_beside_it():
    said = verdict(observed=0.70, n_a=144, n_b=144, p=0.01)
    assert "significant" in said and "resolve" in said


def test_the_default_sd_matches_what_was_measured():
    """546 resolved setups on the 501-symbol Alpaca store, live policy."""
    assert abs(MEASURED_SD - 1.848) < 0.001
