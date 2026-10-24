"""The core-and-satellite plan (execution/satellite.py)."""

from datetime import date, datetime

import pytest

from scanner.execution import satellite as sat

OK = dict(gate=True, gate_name="big caps", top_momentum=True, ret_6m=12.0, above_50d=True,
          earnings_in_days=None, sector="Tech", held_sectors={}, sold_recently=False,
          new_20d_high=False)


def test_a_clean_buy_passes():
    assert sat.buy_refusal(**OK) is None


@pytest.mark.parametrize("change, words", [
    ({"gate": False}, "gate closed"),
    ({"gate": None}, "gate closed"),
    ({"top_momentum": False}, "top third"),
    ({"ret_6m": -1.0}, "6-month"),
    ({"above_50d": False}, "50-day"),
    ({"earnings_in_days": 4}, "earnings in 4"),
    ({"held_sectors": {"Tech": 2}}, "already 2 in Tech"),
    ({"sold_recently": True}, "re-buy only"),
])
def test_each_buy_rule_refuses(change, words):
    assert words in sat.buy_refusal(**{**OK, **change})


def test_rebuy_allowed_on_a_new_20_day_high_and_unknown_sector_uncapped():
    assert sat.buy_refusal(**{**OK, "sold_recently": True, "new_20d_high": True}) is None
    assert sat.buy_refusal(**{**OK, "sector": "unknown", "held_sectors": {"unknown": 5}}) is None
    assert sat.buy_refusal(**{**OK, "earnings_in_days": 6}) is None


def test_gate_and_new_high():
    assert sat.gate_open([100.0] * 199 + [101.0]) is True
    assert sat.gate_open([100.0] * 199 + [99.0]) is False
    assert sat.gate_open([100.0] * 10) is None
    assert sat.new_closing_high([10.0] * 20 + [11.0]) and not sat.new_closing_high([10.0] * 21)


def test_momentum_top_third():
    mom = {f"S{i}": float(i) for i in range(9)}
    assert sat.momentum_top("S8", mom) and sat.momentum_top("S6", mom)
    assert not sat.momentum_top("S5", mom) and not sat.momentum_top("ZZZ", mom)


def test_hard_stop_floor_and_break_even():
    # 2.5 ATR of 2 = 5 under 100 -> the 3% floor (97) is tighter
    assert sat.hard_stop(entry=100, atr=2, high_close=100) == pytest.approx(97.0)
    # wide ATR: 2.5 * 0.5 = 1.25 under -> 98.75 beats the floor
    assert sat.hard_stop(entry=100, atr=0.5, high_close=100) == pytest.approx(98.75)
    # +1 ATR reached: at least break-even
    assert sat.hard_stop(entry=100, atr=2, high_close=102) == pytest.approx(100.0)


def test_trailing_exit_sells_on_a_close_under_the_trail_only_once_armed():
    assert sat.close_exit(entry=100, atr=2, closes_since_buy=[101.0], price=95.5, held_days=2,
                          reports_before_next_session=False) is None          # not armed: the broker stop's job
    why = sat.close_exit(entry=100, atr=1, closes_since_buy=[104, 104.4], price=101.1, held_days=4,
                         reports_before_next_session=False)
    assert why.startswith("trailing exit") and "101.40" in why                 # 104.4 - 3*1; best +4.4%: no lock
    # a +10% run-up giving back more than half: the profit lock comes first (floor 105 > trail 104)
    why = sat.close_exit(entry=100, atr=2, closes_since_buy=[104, 110, 108], price=103.9, held_days=4,
                         reports_before_next_session=False)
    assert why.startswith("profit lock") and "105.00" in why
    assert sat.close_exit(entry=100, atr=2, closes_since_buy=[104, 110], price=105.5, held_days=4,
                          reports_before_next_session=False) is None


def test_time_stop_and_earnings():
    flat = [100.5] * 12
    assert "time stop" in sat.close_exit(entry=100, atr=2, closes_since_buy=flat, price=100.4,
                                         held_days=12, reports_before_next_session=False)
    rising = [100 + i * 0.1 for i in range(12)]
    assert sat.close_exit(entry=100, atr=2, closes_since_buy=rising, price=101.3, held_days=12,
                          reports_before_next_session=False) is None
    assert "earnings" in sat.close_exit(entry=100, atr=2, closes_since_buy=[], price=100, held_days=1,
                                        reports_before_next_session=True)


def test_reports_before_next_session():
    fri, mon = date(2026, 10, 23), date(2026, 10, 26)
    assert sat.reports_before_next_session(fri, "after-close", fri, mon)
    assert sat.reports_before_next_session(fri, "", fri, mon)
    assert not sat.reports_before_next_session(fri, "pre-mkt", fri, mon)
    assert sat.reports_before_next_session(mon, "pre-mkt", fri, mon)
    assert not sat.reports_before_next_session(mon, "after-close", fri, mon)
    assert not sat.reports_before_next_session(None, "", fri, mon)


def test_hold_days_and_clock():
    assert sat.trading_days_between(date(2026, 10, 23), date(2026, 10, 26)) == 1   # Fri -> Mon
    assert not sat.may_drop(2) and sat.may_drop(3)
    assert sat.may_buy_now(datetime(2026, 10, 26, 10, 0, tzinfo=sat.ET))
    assert not sat.may_buy_now(datetime(2026, 10, 26, 9, 59, tzinfo=sat.ET))
    assert sat.in_close_window(datetime(2026, 10, 26, 15, 52, tzinfo=sat.ET))


def test_core_is_bought_in_two_halves_then_only_topped_up():
    assert sat.core_buy_dollars(equity=100_000, core_value=0, pct=50) == 25_000
    assert sat.core_buy_dollars(equity=100_000, core_value=25_000, pct=50) == 25_000
    assert sat.core_buy_dollars(equity=100_000, core_value=46_000, pct=50) == 0.0
    assert sat.core_buy_dollars(equity=100_000, core_value=40_000, pct=50) == 10_000


def test_buy_queue_is_for_its_own_day_only():
    from scanner.execution import buy_queue as bq
    q = bq.make(date(2026, 10, 26), {"AAA": {"reason": "x"}, "BBB": {"reason": "y"}})
    assert list(bq.due(q, today=date(2026, 10, 26), held=["BBB"], pending=[])) == ["AAA"]
    assert bq.due(q, today=date(2026, 10, 27), held=[], pending=[]) == {}
    assert list(bq.without(q, ["AAA"])["items"]) == ["BBB"]


def test_entry_dates_use_the_newest_filled_buy_of_the_book():
    from scanner.execution import plan_run
    orders = [{"symbol": "AAA", "side": "buy", "client_order_id": "gem-AAA-1", "filled_at": "2026-10-20T14:00:00Z"},
              {"symbol": "AAA", "side": "buy", "client_order_id": "gem-AAA-2", "filled_at": "2026-10-22T14:00:00Z"},
              {"symbol": "BBB", "side": "buy", "client_order_id": "orb-BBB-1", "filled_at": "2026-10-22T14:00:00Z"}]
    assert plan_run.entry_dates(orders, ("gemma",)) == {"AAA": date(2026, 10, 22)}


def test_profit_lock_keeps_half_of_a_5pct_gain():
    hold = dict(entry=100, atr=5, held_days=5, reports_before_next_session=False)
    assert sat.close_exit(closes_since_buy=[104.0], price=101.0, **hold) is None          # never +5%: off
    assert sat.close_exit(closes_since_buy=[106.0], price=103.5, **hold) is None          # kept over half
    assert sat.close_exit(closes_since_buy=[106.0], price=102.9, **hold).startswith("profit lock")
