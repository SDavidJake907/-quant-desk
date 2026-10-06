"""The momentum engine: rank, trend filter, three staggered tranches, orders.

What was tested (WORKLIST §31, arm A of momentum_guard_test.py) is what is
built: top 10 by 12-1 momentum, equal weight, three tranches each held three
months with one rebuilt every month, and a tranche held in cash when the S&P
500 closed below its 210-day average. These tests pin that behaviour so the
live engine cannot drift from the measured one.
"""

from __future__ import annotations

from datetime import date

import pytest

from scanner.momentum import book as bk
from scanner.momentum import signal
from scanner.momentum import state


# --- ranking ------------------------------------------------------------

def test_rank_orders_by_momentum_and_drops_unrankable():
    scores = {"AAA": 10.0, "BBB": None, "CCC": 50.0, "DDD": -5.0}
    assert signal.rank(scores, top_n=10) == ("CCC", "AAA", "DDD")


def test_rank_keeps_only_top_n():
    scores = {f"S{i:02d}": float(i) for i in range(30)}
    assert signal.rank(scores, top_n=10) == tuple(f"S{i:02d}" for i in range(29, 19, -1))


def test_rank_breaks_ties_by_symbol_so_runs_are_repeatable():
    assert signal.rank({"ZZZ": 1.0, "AAA": 1.0, "MMM": 1.0}, top_n=2) == ("AAA", "MMM")


# --- trend filter -------------------------------------------------------

def test_trend_on_when_last_close_above_210_day_average():
    closes = [100.0] * 219 + [101.0]
    assert signal.trend_ok(closes) is True


def test_trend_off_when_last_close_below_average():
    closes = [100.0] * 219 + [99.0]
    assert signal.trend_ok(closes) is False


def test_trend_off_without_enough_history():
    # Conservative, as in the backtest: no reading means cash.
    assert signal.trend_ok([100.0] * 150) is False


# --- which tranche is due ----------------------------------------------

def test_one_tranche_per_month_in_rotation():
    assert bk.tranche_for(date(2026, 12, 1)) != bk.tranche_for(date(2027, 1, 4))
    assert bk.tranche_for(date(2026, 12, 1)) == bk.tranche_for(date(2027, 3, 1))


def test_due_is_none_once_the_tranche_was_rebuilt_this_month():
    book = bk.new_book(90_000.0)
    when = date(2026, 12, 1)
    idx = bk.tranche_for(when)
    rebuilt = bk.replace_tranche(book, bk.Tranche(index=idx, formed=when, holdings={}, cash=30_000.0))
    assert bk.due(rebuilt, date(2026, 12, 15)) is None
    assert bk.due(book, when) == idx


# --- rebuilding a tranche ----------------------------------------------

def test_new_book_splits_capital_into_three_cash_tranches():
    book = bk.new_book(90_000.0)
    assert [t.cash for t in book.tranches] == [30_000.0] * 3
    assert all(not t.holdings for t in book.tranches)


def test_rebuild_invested_is_equal_weight_whole_shares_and_keeps_change_as_cash():
    t = bk.Tranche(index=0, formed=None, holdings={}, cash=30_000.0)
    out = bk.rebuild(t, date(2026, 12, 1), picks=("A", "B", "C"),
                     prices={"A": 100.0, "B": 333.0, "C": 7.0}, invested=True)
    assert out.holdings == {"A": 100, "B": 30, "C": 1428}
    assert out.cash == pytest.approx(30_000.0 - 100 * 100.0 - 30 * 333.0 - 1428 * 7.0)
    assert out.formed == date(2026, 12, 1)


def test_rebuild_sells_old_holdings_at_todays_prices_first():
    t = bk.Tranche(index=1, formed=date(2026, 9, 1), holdings={"OLD": 10}, cash=0.0)
    out = bk.rebuild(t, date(2026, 12, 1), picks=(), prices={"OLD": 50.0}, invested=False)
    assert out.holdings == {}
    assert out.cash == pytest.approx(500.0)


def test_rebuild_in_cash_when_trend_is_off():
    t = bk.Tranche(index=2, formed=None, holdings={}, cash=30_000.0)
    out = bk.rebuild(t, date(2026, 12, 1), picks=("A",), prices={"A": 10.0}, invested=False)
    assert out.holdings == {} and out.cash == 30_000.0


def test_value_refuses_to_guess_a_missing_price():
    t = bk.Tranche(index=0, formed=None, holdings={"GONE": 5}, cash=0.0)
    with pytest.raises(KeyError):
        bk.value(t, prices={})


def test_rebuild_does_not_mutate_its_input():
    t = bk.Tranche(index=0, formed=None, holdings={"X": 1}, cash=10.0)
    bk.rebuild(t, date(2026, 12, 1), picks=("Y",), prices={"X": 5.0, "Y": 5.0}, invested=True)
    assert t.holdings == {"X": 1} and t.cash == 10.0


# --- orders --------------------------------------------------------------

def test_target_adds_up_shares_across_tranches():
    book = bk.Book(tranches=(
        bk.Tranche(index=0, formed=None, holdings={"A": 10, "B": 5}, cash=0.0),
        bk.Tranche(index=1, formed=None, holdings={"A": 3}, cash=0.0),
        bk.Tranche(index=2, formed=None, holdings={}, cash=0.0),
    ))
    assert bk.target(book) == {"A": 13, "B": 5}


def test_orders_net_the_difference_sells_first_and_skip_no_change():
    current = {"A": 10, "B": 5, "C": 7}
    target = {"A": 13, "C": 7, "D": 4}
    assert bk.orders(current, target) == (("B", "sell", 5), ("A", "buy", 3), ("D", "buy", 4))


# --- state ----------------------------------------------------------------

def test_state_round_trips(tmp_path):
    book = bk.Book(tranches=(
        bk.Tranche(index=0, formed=date(2026, 12, 1), holdings={"A": 3}, cash=12.5),
        bk.Tranche(index=1, formed=None, holdings={}, cash=30_000.0),
        bk.Tranche(index=2, formed=None, holdings={}, cash=30_000.0),
    ))
    path = tmp_path / "book.json"
    state.save(book, path)
    assert state.load(path) == book


def test_load_missing_state_returns_none(tmp_path):
    assert state.load(tmp_path / "nope.json") is None
