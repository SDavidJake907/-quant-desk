"""Position-sizing tests.

Sizing is where a backtest's R-multiples either become real or become
decoration. If trades are not equally weighted by risk, the measured +0.206R
average describes a portfolio nobody is running.
"""

from __future__ import annotations

import pytest

from scanner.execution.sizing import size_position


def test_risk_is_constant_regardless_of_stop_distance():
    """The whole point of sizing off the stop. A tight stop buys more shares
    so that both trades lose the same money when they fail."""
    tight = size_position(100_000, entry=100.0, stop=99.0, risk_pct=1.0,
                          max_position_pct=100.0)
    wide = size_position(100_000, entry=100.0, stop=96.0, risk_pct=1.0,
                         max_position_pct=100.0)
    assert tight.shares > wide.shares
    assert tight.risk_dollars == pytest.approx(wide.risk_dollars, rel=0.02)


def test_risk_dollars_track_the_budget():
    sizing = size_position(50_000, entry=100.0, stop=98.0, risk_pct=2.0,
                           max_position_pct=100.0)
    assert sizing.risk_dollars == pytest.approx(1000.0, rel=0.01)


def test_price_does_not_change_the_risk():
    """A $9 stock and a $900 stock must risk the same amount."""
    cheap = size_position(100_000, entry=9.0, stop=8.82, risk_pct=1.0,
                          max_position_pct=100.0)
    dear = size_position(100_000, entry=900.0, stop=882.0, risk_pct=1.0,
                         max_position_pct=100.0)
    assert cheap.risk_dollars == pytest.approx(dear.risk_dollars, rel=0.02)


# ---- the caps ----------------------------------------------------------

def test_a_very_tight_stop_is_capped_by_position_size():
    """Without the cap a 0.1% stop sizes to ten times the account."""
    sizing = size_position(100_000, entry=100.0, stop=99.9,
                           risk_pct=1.0, max_position_pct=20.0)
    assert sizing.position_dollars <= 20_000 + 100
    assert sizing.limited_by == "position cap"


def test_buying_power_caps_the_order():
    sizing = size_position(100_000, entry=100.0, stop=95.0,
                           risk_pct=50.0, buying_power=5_000)
    assert sizing.position_dollars <= 5_000
    assert sizing.limited_by == "buying power"


def test_the_binding_constraint_is_named():
    sizing = size_position(100_000, entry=100.0, stop=95.0, risk_pct=1.0)
    assert sizing.limited_by == "risk budget"


def test_risking_one_percent_behind_a_tight_stop_demands_most_of_the_account():
    """The concentration trap, stated as arithmetic: position needed equals
    risk_pct / stop_pct. Behind this screen's 1.5% stops, a 1% risk budget
    asks for 67% of the account in ONE name — so the cap binds and effective
    risk lands near 0.3%. That is the cap working, not a bug."""
    uncapped = size_position(100_000, entry=100.0, stop=98.5, risk_pct=1.0,
                             max_position_pct=100.0)
    assert uncapped.position_dollars > 60_000

    capped = size_position(100_000, entry=100.0, stop=98.5, risk_pct=1.0,
                           max_position_pct=20.0)
    assert capped.limited_by == "position cap"
    assert capped.risk_dollars < 400.0


# ---- refusals ----------------------------------------------------------

def test_a_stop_above_entry_is_not_a_trade():
    sizing = size_position(100_000, entry=100.0, stop=101.0)
    assert sizing.shares == 0
    assert "stop" in sizing.limited_by


def test_no_equity_means_no_position():
    assert size_position(0.0, entry=100.0, stop=95.0).shares == 0


def test_a_share_you_cannot_afford_is_skipped_not_rounded_up():
    """Rounding up would silently breach the risk budget on exactly the
    trades that are already too large for the account."""
    sizing = size_position(500.0, entry=900.0, stop=880.0, risk_pct=1.0)
    assert sizing.shares == 0
    assert sizing.is_tradeable is False


def test_shares_are_whole_numbers():
    sizing = size_position(12_345.67, entry=97.31, stop=95.02, risk_pct=1.0)
    assert sizing.shares == int(sizing.shares)
    assert sizing.shares >= 1


def test_sizing_never_exceeds_the_risk_budget():
    """Flooring, not rounding — the budget is a ceiling."""
    for equity in (1_000, 7_777, 100_000):
        sizing = size_position(equity, entry=51.37, stop=50.11, risk_pct=1.0,
                               max_position_pct=100.0)
        assert sizing.risk_dollars <= equity * 0.01 + 1e-9
