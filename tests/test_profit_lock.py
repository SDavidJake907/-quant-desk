import pytest

from scanner.execution import profit_lock as pl


def test_no_lock_until_up_one_percent():
    assert pl.lock_stop(entry=100.0, high=100.9) == pytest.approx(97.0)   # the loss floor


def test_lock_at_one_percent_is_just_above_entry():
    assert pl.lock_stop(entry=100.0, high=101.0) == pytest.approx(100.2)


def test_trail_takes_over_once_the_high_is_far_enough():
    # 2% under a 110 high = 107.8, above the 100.2 lock
    assert pl.lock_stop(entry=100.0, high=110.0) == pytest.approx(107.8)


def test_update_tracks_the_high_and_never_lowers_it():
    s = pl.update({}, "X", entry=100.0, price=105.0)
    s = pl.update(s, "X", entry=100.0, price=103.0)
    assert s["X"]["high"] == 105.0


def test_update_is_immutable():
    before = {"X": {"entry": 100.0, "high": 101.0}}
    after = pl.update(before, "X", entry=100.0, price=104.0)
    assert before["X"]["high"] == 101.0 and after["X"]["high"] == 104.0


def test_a_new_entry_price_resets_the_high():
    """A top-up or a fresh buy after a sell changes the average: start again."""
    s = pl.update({"X": {"entry": 100.0, "high": 120.0}}, "X", entry=95.0, price=96.0)
    assert s["X"] == {"entry": 95.0, "high": 96.0}


def test_prune_drops_symbols_no_longer_held():
    assert pl.prune({"X": {"entry": 1, "high": 1}, "Y": {"entry": 1, "high": 1}}, {"Y"}) == \
        {"Y": {"entry": 1, "high": 1}}


def test_stop_orders_finds_held_legs_with_ids():
    orders = [{"symbol": "X", "side": "buy", "status": "filled", "legs": [
        {"id": "t1", "symbol": "X", "side": "sell", "type": "limit", "status": "held", "limit_price": "120"},
        {"id": "s1", "symbol": "X", "side": "sell", "type": "stop", "status": "held", "stop_price": "95.5"}]},
        {"id": "s2", "symbol": "Y", "side": "sell", "type": "stop", "status": "canceled", "stop_price": "10"}]
    assert pl.stop_orders(orders) == {"X": ("s1", 95.5)}


def test_action_raise_when_the_lock_is_above_the_broker_stop():
    assert pl.action(price=105.0, lock=103.0, broker_stop=95.0) == "raise"


def test_action_none_when_the_broker_stop_is_already_there():
    assert pl.action(price=105.0, lock=103.0, broker_stop=103.0) == "none"


def test_action_sell_when_the_price_is_at_or_under_the_lock():
    assert pl.action(price=102.9, lock=103.0, broker_stop=95.0) == "sell"


def test_action_none_without_a_lock():
    assert pl.action(price=99.0, lock=None, broker_stop=95.0) == "none"
