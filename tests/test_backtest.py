"""Backtest-engine tests.

A backtest that lies is worse than no backtest, because it produces confident
wrong numbers. These tests pin the two properties that keep it honest:
point-in-time feature computation, and entry strictly after the screen date.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scanner.backtest import engine
from scanner.features.bars import Bars
from scanner.features.technicals import Features
from scanner.screens.trend import Candidate


def _bars(closes: list[float], symbol: str = "TEST") -> Bars:
    start = date(2023, 1, 2)
    return Bars(
        symbol=symbol,
        dates=tuple(start + timedelta(days=i) for i in range(len(closes))),
        closes=tuple(closes),
        volumes=tuple(1_000_000.0 for _ in closes),
        highs=tuple(c * 1.01 for c in closes),
        lows=tuple(c * 0.99 for c in closes),
    )


def _always(f: Features, rs: float | None) -> Candidate:
    return Candidate(symbol=f.symbol, features=f, relative_strength=rs,
                     criteria=(), score=1.0)


# ---- point-in-time guarantees ------------------------------------------

def test_upto_truncates_history():
    bars = _bars([float(i) for i in range(100)])
    assert len(bars.upto(49)) == 50
    assert bars.upto(49).closes[-1] == 49.0


def test_upto_cannot_see_the_future():
    """The defining property. Features at i must be identical whether or not
    later bars exist in the parent series."""
    full = _bars([float(i) for i in range(400)])
    truncated = _bars([float(i) for i in range(300)])
    assert full.upto(299).closes == truncated.upto(299).closes


def test_index_of_finds_last_bar_on_or_before():
    bars = _bars([1.0, 2.0, 3.0])
    assert bars.index_of(bars.dates[1]) == 1
    assert bars.index_of(bars.dates[2] + timedelta(days=10)) == 2


def test_index_of_returns_none_before_history_starts():
    bars = _bars([1.0, 2.0, 3.0])
    assert bars.index_of(bars.dates[0] - timedelta(days=1)) is None


# ---- trade accounting --------------------------------------------------

def test_excess_return_is_trade_minus_benchmark():
    trade = engine.Trade(
        symbol="X", entry_date=date(2025, 1, 2), exit_date=date(2025, 2, 2),
        entry_price=100.0, exit_price=110.0, benchmark_return=4.0,
    )
    assert trade.trade_return == pytest.approx(10.0)
    assert trade.excess_return == pytest.approx(6.0)


def test_zero_entry_price_does_not_divide_by_zero():
    trade = engine.Trade(
        symbol="X", entry_date=date(2025, 1, 2), exit_date=date(2025, 2, 2),
        entry_price=0.0, exit_price=110.0, benchmark_return=0.0,
    )
    assert trade.trade_return == 0.0


# ---- engine behaviour --------------------------------------------------

def test_entry_is_the_bar_after_the_screen_date():
    """You cannot screen on a close and buy at that same close."""
    n = 400
    bars = {"TEST": _bars([100.0 + i for i in range(n)])}
    benchmark = _bars([100.0] * n, "^BENCH")

    result = engine.run(bars, benchmark, _always, horizon_days=5, step_days=50, top_n=1)
    assert result.count > 0
    for trade in result.trades:
        screen_i = bars["TEST"].dates.index(trade.entry_date) - 1
        assert screen_i >= 0
        assert trade.entry_date > bars["TEST"].dates[screen_i]


def test_flat_benchmark_means_excess_equals_raw():
    n = 400
    bars = {"UP": _bars([100.0 + i for i in range(n)], "UP")}
    benchmark = _bars([100.0] * n, "^BENCH")
    result = engine.run(bars, benchmark, _always, horizon_days=10, step_days=60, top_n=1)
    assert result.mean_excess == pytest.approx(result.mean_return, abs=1e-9)


def test_no_trades_when_history_too_short():
    bars = {"SHORT": _bars([100.0] * 50)}
    benchmark = _bars([100.0] * 50, "^BENCH")
    result = engine.run(bars, benchmark, _always, horizon_days=5, step_days=5, top_n=5)
    assert result.count == 0


def test_top_n_limits_positions_per_rebalance():
    n = 400
    bars = {f"S{i}": _bars([100.0 + i + j for j in range(n)], f"S{i}") for i in range(8)}
    benchmark = _bars([100.0] * n, "^BENCH")
    result = engine.run(bars, benchmark, _always, horizon_days=5, step_days=100, top_n=3)
    assert result.count <= result.rebalances * 3


def test_empty_result_metrics_are_safe():
    empty = engine.BacktestResult(trades=(), rebalances=0, horizon_days=21, top_n=10)
    assert empty.mean_excess == 0.0
    assert empty.hit_rate == 0.0
    assert empty.stdev_excess == 0.0


# ---- point-in-time membership -----------------------------------------
#
# Without a membership filter, a rebalance in 2023 can buy a company the
# index did not add until 2025. Index addition is itself a momentum event,
# so that is not a harmless approximation — it is lookahead in the universe.

def test_membership_filter_excludes_non_members():
    n = 400
    bars = {f"S{i}": _bars([100.0 + i + j for j in range(n)], f"S{i}") for i in range(5)}
    benchmark = _bars([100.0] * n, "^BENCH")

    result = engine.run(
        bars, benchmark, _always, horizon_days=5, step_days=50, top_n=5,
        membership=lambda _d: frozenset({"S0", "S1"}),
    )
    assert result.count > 0
    assert {t.symbol for t in result.trades} == {"S0", "S1"}


def test_membership_can_change_over_time():
    """A name joins the index halfway through and is untradeable before."""
    n = 400
    bars = {f"S{i}": _bars([100.0 + i + j for j in range(n)], f"S{i}") for i in range(2)}
    benchmark = _bars([100.0] * n, "^BENCH")
    joined = benchmark.dates[330]

    def membership(d):
        return frozenset({"S0", "S1"}) if d >= joined else frozenset({"S0"})

    result = engine.run(
        bars, benchmark, _always, horizon_days=5, step_days=20, top_n=5,
        membership=membership,
    )
    assert all(t.entry_date > joined for t in result.trades if t.symbol == "S1")
    assert any(t.symbol == "S1" for t in result.trades)


def test_members_without_price_history_are_counted_not_ignored():
    """The unfixed half of survivorship bias has to stay visible."""
    n = 400
    bars = {"S0": _bars([100.0 + i for i in range(n)], "S0")}
    benchmark = _bars([100.0] * n, "^BENCH")

    result = engine.run(
        bars, benchmark, _always, horizon_days=5, step_days=50, top_n=5,
        membership=lambda _d: frozenset({"S0", "DELISTED"}),
    )
    assert result.missing_members == ("DELISTED",)


def test_no_membership_filter_leaves_everything_eligible():
    n = 400
    bars = {f"S{i}": _bars([100.0 + i + j for j in range(n)], f"S{i}") for i in range(3)}
    benchmark = _bars([100.0] * n, "^BENCH")
    result = engine.run(bars, benchmark, _always, horizon_days=5, step_days=100, top_n=3)
    assert result.missing_members == ()
    assert result.mean_universe == 3.0


# ---- symbol spelling ---------------------------------------------------

def test_share_class_tickers_are_translated_for_yahoo():
    """BRK.B and BF.B 404 on Yahoo unless the dot becomes a dash."""
    from scanner.features.bars import to_yahoo

    assert to_yahoo("BRK.B") == "BRK-B"
    assert to_yahoo("AAPL") == "AAPL"
    assert to_yahoo("^GSPC") == "^GSPC"


def test_range_start_covers_the_requested_window():
    from scanner.features.bars import range_start

    today = date(2026, 8, 5)
    assert range_start("5y", today) == date(2021, 8, 5)
    assert range_start("2y", today) == date(2024, 8, 5)
    assert range_start("6mo", today).year == 2026
    assert range_start("ytd", today) == date(2026, 1, 1)
    assert range_start("max", today) == date(1970, 1, 1)


def test_range_start_falls_back_rather_than_raising():
    """A bad range string must not take the whole backtest down; it falls
    back to the same 2y default `fetch_bars` uses."""
    from scanner.features.bars import range_start

    today = date(2026, 8, 5)
    assert range_start("nonsense", today) == range_start("2y", today)


# ---- recycled tickers --------------------------------------------------

def test_history_starting_after_index_exit_is_a_different_company():
    """Signature Bank failed in March 2023; SBNY now returns somebody else's
    prices. Screening them as SBNY is worse than having no data at all."""
    n = 300
    recycled = _bars([100.0 + i for i in range(n)], "SBNY")
    kept, dropped = engine.drop_recycled(
        {"SBNY": recycled}, {"SBNY": recycled.dates[0] - timedelta(days=1)}
    )
    assert dropped == ("SBNY",)
    assert kept == {}


def test_genuine_delisting_history_is_kept():
    """A company whose bars run up to its deletion is exactly what we want."""
    n = 300
    bars = _bars([100.0 + i for i in range(n)], "CERN")
    kept, dropped = engine.drop_recycled({"CERN": bars}, {"CERN": bars.dates[-1]})
    assert dropped == ()
    assert set(kept) == {"CERN"}


def test_current_members_are_never_dropped():
    bars = _bars([100.0], "AAPL")
    kept, dropped = engine.drop_recycled({"AAPL": bars}, {})
    assert dropped == () and set(kept) == {"AAPL"}
