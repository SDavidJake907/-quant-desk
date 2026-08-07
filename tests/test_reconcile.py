"""Reconciliation tests.

A paper run is only evidence if every intent can be traced to an outcome. The
two failures that would quietly destroy that are counting an unfilled order as
a trade, and losing an order the broker knows about but the log does not.
"""

from __future__ import annotations

import pytest

from scanner.execution.reconcile import (
    CLOSED_STOP,
    CLOSED_TARGET,
    EXPIRED,
    FILLED_OPEN,
    REJECTED,
    WORKING,
    Summary,
    classify,
    reconcile,
)

INTENT = {
    "symbol": "SUPN", "shares": 418, "entry_price": 47.85,
    "stop_price": 46.56, "target_price": 52.50,
}
INTENTS = {"smc-SUPN-1": INTENT}


def _order(status="new", fill=None, legs=(), coid="smc-SUPN-1"):
    return {
        "client_order_id": coid, "id": "abc", "symbol": "SUPN", "qty": "418",
        "status": status, "filled_avg_price": fill, "limit_price": "47.85",
        "legs": list(legs), "submitted_at": "2026-08-06T15:05:09Z",
    }


def _leg(kind, fill=None, status="held"):
    return {"type": kind, "status": status, "filled_avg_price": fill}


# ---- classification ----------------------------------------------------

def test_an_unfilled_order_is_working_not_a_trade():
    """Counting this as a trade would invent a result out of an order that
    never happened — and inflate the fill rate to 100% by construction."""
    assert classify(_order())[0] == WORKING


def test_an_expired_order_never_became_a_trade():
    assert classify(_order(status="expired"))[0] == EXPIRED
    assert classify(_order(status="canceled"))[0] == EXPIRED


def test_a_rejected_order_is_reported_as_such():
    assert classify(_order(status="rejected"))[0] == REJECTED


def test_a_filled_entry_with_live_legs_is_an_open_position():
    state, entry, exit_ = classify(_order(status="filled", fill="47.90",
                                          legs=[_leg("limit"), _leg("stop")]))
    assert state == FILLED_OPEN
    assert entry == pytest.approx(47.90)
    assert exit_ is None


def test_a_filled_target_leg_closes_the_trade_as_a_win():
    state, entry, exit_ = classify(_order(
        status="filled", fill="47.90",
        legs=[_leg("limit", "52.50", "filled"), _leg("stop", None, "canceled")]))
    assert state == CLOSED_TARGET
    assert exit_ == pytest.approx(52.50)


def test_a_filled_stop_leg_closes_the_trade_as_a_loss():
    state, _, exit_ = classify(_order(
        status="filled", fill="47.90",
        legs=[_leg("limit", None, "canceled"), _leg("stop", "46.56", "filled")]))
    assert state == CLOSED_STOP
    assert exit_ == pytest.approx(46.56)


# ---- R ------------------------------------------------------------------

def test_r_uses_the_intended_risk_not_the_filled_risk():
    """Position size came from the planned stop distance, so that is the
    denominator the backtest's R-multiples describe."""
    trade = reconcile(INTENTS, [_order(
        status="filled", fill="47.90",
        legs=[_leg("limit", "52.50", "filled")])])[0]
    assert trade.intended_risk == pytest.approx(1.29)
    assert trade.r_multiple() == pytest.approx((52.50 - 47.90) / 1.29)


def test_a_stopped_trade_is_about_minus_one_r():
    trade = reconcile(INTENTS, [_order(
        status="filled", fill="47.85",
        legs=[_leg("stop", "46.56", "filled")])])[0]
    assert trade.r_multiple() == pytest.approx(-1.0)


def test_an_open_position_needs_a_mark_to_be_priced():
    trade = reconcile(INTENTS, [_order(status="filled", fill="47.85",
                                       legs=[_leg("limit"), _leg("stop")])])[0]
    assert trade.r_multiple() is None
    assert trade.r_multiple(mark=49.14) == pytest.approx(1.0)


def test_an_unfilled_order_has_no_r():
    assert reconcile(INTENTS, [_order()])[0].r_multiple(mark=50.0) is None


def test_slippage_is_the_gap_between_plan_and_fill():
    trade = reconcile(INTENTS, [_order(status="filled", fill="47.95")])[0]
    assert trade.slippage == pytest.approx(0.10)


# ---- joining -------------------------------------------------------------

def test_an_order_the_log_does_not_know_about_is_still_reported():
    """An untracked position is the one you most need to see."""
    trades = reconcile({}, [_order(coid="manual-1", status="filled", fill="10.0")])
    assert len(trades) == 1 and trades[0].symbol == "SUPN"


def test_an_intent_with_no_order_is_not_silently_dropped():
    trades = reconcile(INTENTS, [])
    assert len(trades) == 1
    assert trades[0].state == REJECTED


# ---- summary -------------------------------------------------------------

def test_fill_rate_is_the_measurement_the_backtest_could_not_make():
    """A limit at the trigger close only fills if price comes back, so live
    fill rate is unknown until measured — and it is where live results and
    backtest results diverge without either being wrong."""
    trades = reconcile(
        {f"c{i}": {**INTENT} for i in range(4)},
        [_order(coid="c0", status="filled", fill="47.85"),
         _order(coid="c1", status="filled", fill="47.85"),
         _order(coid="c2"),
         _order(coid="c3", status="expired")],
    )
    summary = Summary(trades=trades)
    # An expired order WAS sent — it reached the broker and simply never
    # filled. Excluding it would flatter the fill rate by hiding the misses.
    assert summary.sent == 4
    assert summary.filled == 2
    assert summary.fill_rate == pytest.approx(50.0)


def test_expectancy_counts_only_closed_trades():
    trades = reconcile(
        {"a": {**INTENT}, "b": {**INTENT}},
        [_order(coid="a", status="filled", fill="47.85",
                legs=[_leg("stop", "46.56", "filled")]),
         _order(coid="b", status="filled", fill="47.85",
                legs=[_leg("limit"), _leg("stop")])],
    )
    summary = Summary(trades=trades)
    assert len(summary.closed) == 1
    assert summary.open_positions == 1
    assert summary.expectancy == pytest.approx(-1.0)


def test_an_empty_run_is_safe():
    summary = Summary(trades=())
    assert summary.fill_rate == 0.0
    assert summary.expectancy == 0.0
    assert summary.realized_r == 0.0


# ---- the time limit ----------------------------------------------------

def test_position_age_is_counted_in_bars_not_calendar_time():
    """The backtest's limit was 60 BARS. Counting calendar hours instead
    would close positions early over a weekend and late over a holiday —
    measuring a different strategy from the one that was validated."""
    import importlib.util
    import sys
    from datetime import datetime, timedelta

    from scanner.features.intraday import EXCHANGE_TZ, IntradayBars

    spec = importlib.util.spec_from_file_location(
        "manage_positions", "scripts/manage_positions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["manage_positions"] = module
    spec.loader.exec_module(module)

    start = datetime(2026, 8, 3, 9, 30, tzinfo=EXCHANGE_TZ)
    times = tuple(start + timedelta(minutes=30 * i) for i in range(10))
    bars = IntradayBars(
        symbol="X", interval_minutes=30, alignment="session", times=times,
        opens=(1.0,) * 10, highs=(1.0,) * 10, lows=(1.0,) * 10,
        closes=(1.0,) * 10, volumes=(1.0,) * 10,
    )
    assert module.bars_since(bars, times[0]) == 9
    assert module.bars_since(bars, times[-1]) == 0
    assert module.bars_since(bars, start - timedelta(days=30)) == 10
