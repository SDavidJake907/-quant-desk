"""Transaction-cost tests.

Costs are charged against price while results are measured in R, so the same
friction is trivial on a wide stop and decisive on a tight one. These pin that
relationship, because it is the reason an intraday edge can look real and be
untradeable.
"""

from __future__ import annotations

import pytest

from scanner.backtest.costs import (
    FREE,
    CostModel,
    breakeven_bps,
    net_r_multiple,
)
from scanner.backtest.setups import STOP, TARGET, Outcome, Report


def _outcome(entry=100.0, stop=99.0, exit_price=102.0) -> Outcome:
    return Outcome(symbol="X", entry_index=0, entry_price=entry, stop_price=stop,
                   target_price=entry * 1.05, exit_index=5, exit_price=exit_price,
                   reason=TARGET if exit_price > entry else STOP)


# ---- the mechanics -----------------------------------------------------

def test_you_buy_above_the_quote_and_sell_below_it():
    costs = CostModel(spread_bps=10.0, slippage_bps=0.0)
    assert costs.buy_price(100.0) > 100.0
    assert costs.sell_price(100.0) < 100.0


def test_half_the_spread_is_crossed_each_side():
    costs = CostModel(spread_bps=10.0, slippage_bps=0.0)
    assert costs.per_side_bps == pytest.approx(5.0)
    assert costs.round_trip_bps == pytest.approx(10.0)


def test_slippage_is_charged_on_top_each_side():
    costs = CostModel(spread_bps=10.0, slippage_bps=3.0)
    assert costs.per_side_bps == pytest.approx(8.0)
    assert costs.round_trip_bps == pytest.approx(16.0)


def test_free_model_changes_nothing():
    assert FREE.buy_price(100.0) == 100.0
    assert net_r_multiple(100.0, 102.0, 99.0, FREE) == pytest.approx(2.0)


def test_commission_is_per_share_per_side():
    costs = CostModel(spread_bps=0.0, slippage_bps=0.0, commission_per_share=0.01)
    assert costs.buy_price(100.0) == pytest.approx(100.01)
    assert costs.sell_price(100.0) == pytest.approx(99.99)


# ---- why tight stops hurt ----------------------------------------------

def test_the_same_friction_costs_more_R_on_a_tighter_stop():
    """The whole point. 10bps is nothing on an 8% stop and a third of the
    edge on a 1% stop — and this screen produces 1% stops."""
    costs = CostModel(spread_bps=10.0, slippage_bps=0.0)
    wide = net_r_multiple(100.0, 108.0, 92.0, costs)     # 8% risk
    tight = net_r_multiple(100.0, 101.0, 99.0, costs)    # 1% risk
    wide_gross = net_r_multiple(100.0, 108.0, 92.0, FREE)
    tight_gross = net_r_multiple(100.0, 101.0, 99.0, FREE)
    assert (wide_gross - wide) < (tight_gross - tight)


def test_costs_always_reduce_the_result():
    costs = CostModel(spread_bps=8.0, slippage_bps=2.0)
    for exit_price in (95.0, 100.0, 110.0):
        gross = net_r_multiple(100.0, exit_price, 99.0, FREE)
        assert net_r_multiple(100.0, exit_price, 99.0, costs) < gross


def test_risk_denominator_is_the_planned_risk():
    """Position size was set from the intended stop distance, so that is what
    the result is measured against — costs hit the numerator, as in a real
    account."""
    costs = CostModel(spread_bps=20.0, slippage_bps=0.0)
    assert net_r_multiple(100.0, 102.0, 99.0, costs) == pytest.approx(
        (costs.sell_price(102.0) - costs.buy_price(100.0)) / 1.0
    )


def test_a_non_positive_risk_is_not_a_trade():
    assert net_r_multiple(100.0, 110.0, 100.0, FREE) == 0.0


# ---- breakeven ---------------------------------------------------------

def test_breakeven_scales_with_edge_and_stop_distance():
    """An edge of 0.10R on a 1%-risk trade dies at 10bps round trip."""
    assert breakeven_bps(0.10, [1.0]) == pytest.approx(10.0)
    assert breakeven_bps(0.20, [1.0]) == pytest.approx(20.0)
    assert breakeven_bps(0.10, [2.0]) == pytest.approx(20.0)


def test_breakeven_uses_the_harmonic_mean_not_the_arithmetic_one():
    """Cost in R scales with 1/risk, so a few very tight stops dominate the
    average. The arithmetic mean overstated survivable friction by 2x and
    reported a set as viable while it was already losing money."""
    risks = [0.25, 0.25, 3.0, 3.0]           # arithmetic mean 1.625
    naive = 100.0 * 0.10 * (sum(risks) / len(risks))
    assert breakeven_bps(0.10, risks) < naive / 2


def test_no_risk_means_no_breakeven():
    assert breakeven_bps(0.5, [0.0]) == 0.0
    assert breakeven_bps(0.5, []) == 0.0


def test_a_losing_strategy_has_no_friction_budget():
    assert breakeven_bps(-0.2, [1.0]) == 0.0


# ---- reporting ---------------------------------------------------------

def test_report_nets_out_costs():
    report = Report(label="x", outcomes=tuple(_outcome() for _ in range(5)))
    assert report.net_expectancy(CostModel()) < report.expectancy


def test_report_breakeven_uses_harmonic_risk():
    report = Report(label="x", outcomes=(_outcome(100.0, 99.0, 102.0),))
    assert report.mean_risk_pct == pytest.approx(1.0)
    assert report.harmonic_risk_pct == pytest.approx(1.0)
    assert report.breakeven_bps == pytest.approx(report.expectancy * 100.0)


def test_harmonic_risk_never_exceeds_arithmetic():
    outcomes = (_outcome(100.0, 99.75, 101.0), _outcome(100.0, 97.0, 101.0))
    report = Report(label="x", outcomes=outcomes)
    assert report.harmonic_risk_pct <= report.mean_risk_pct


def test_empty_report_costs_are_safe():
    empty = Report(label="none", outcomes=())
    assert empty.net_expectancy(CostModel()) == 0.0
    assert empty.breakeven_bps == 0.0
