"""Extended-hours exits, and the invariants that make them safe to run unwatched.

The property that matters most is the one `flatten.py` is also pinned on: this
code can only ever CLOSE. It runs at hours when nobody is looking, and the
worst failure available to it is opening a position it believes is an exit.
"""

from __future__ import annotations

import pytest

from scanner.execution.extended_exit import (
    DEFAULT_SLIPPAGE_BPS,
    exit_payload,
    marketable_limit,
    plan_extended_exit,
)


def _position(symbol: str, qty: str, price: str = "100.00") -> dict:
    return {
        "symbol": symbol, "qty": qty, "current_price": price,
        "avg_entry_price": "99.00", "side": "long" if float(qty) > 0 else "short",
    }


def _order(order_id: str, symbol: str = "ACN", status: str = "new") -> dict:
    return {"id": order_id, "symbol": symbol, "status": status}


# --- pricing ------------------------------------------------------------

def test_a_sell_prices_below_the_reference_so_it_can_trade():
    assert marketable_limit("sell", 100.0, 50.0) == 99.50


def test_a_buy_prices_above_the_reference():
    assert marketable_limit("buy", 100.0, 50.0) == 100.50


def test_zero_slippage_prices_at_the_reference():
    assert marketable_limit("sell", 100.0, 0.0) == 100.0


def test_an_unusable_reference_is_refused_not_guessed():
    """A stale or zero overnight price would otherwise price an order
    anywhere at all."""
    with pytest.raises(ValueError):
        marketable_limit("sell", 0.0, 50.0)


def test_negative_slippage_is_refused():
    with pytest.raises(ValueError):
        marketable_limit("sell", 100.0, -10.0)


def test_prices_are_rounded_to_a_tick():
    price = marketable_limit("sell", 33.333, 37.0)
    assert price == round(price, 2)


# --- planning -----------------------------------------------------------

def test_a_long_position_closes_with_a_sell():
    plan = plan_extended_exit([_position("ACN", "15")], [])
    assert [(leg.symbol, leg.side, leg.shares) for leg in plan.legs] == [
        ("ACN", "sell", 15)
    ]


def test_a_short_position_closes_with_a_buy_not_another_sell():
    plan = plan_extended_exit([_position("ACN", "-15")], [])
    assert [(leg.side, leg.shares) for leg in plan.legs] == [("buy", 15)]


def test_a_flat_book_produces_an_empty_plan():
    assert plan_extended_exit([], []).is_empty


def test_a_zero_quantity_position_is_not_an_exit():
    """Re-running after a fill must not send a second order. This is what
    makes the script idempotent."""
    assert plan_extended_exit([_position("ACN", "0")], []).is_empty


def test_working_orders_are_cancelled_first():
    """A resting bracket leg reserves the shares, so an exit underneath one is
    rejected for insufficient quantity."""
    plan = plan_extended_exit(
        [_position("ACN", "15")],
        [_order("abc"), _order("def", status="filled")],
    )
    assert plan.cancel_first == ("abc",)


def test_quantities_come_from_the_broker_not_from_local_state():
    plan = plan_extended_exit([_position("ACN", "7")], [])
    assert plan.legs[0].shares == 7


def test_a_position_without_a_usable_price_is_skipped_loudly():
    """Better no exit than an exit resting at a price it can never fill."""
    plan = plan_extended_exit(
        [{"symbol": "ACN", "qty": "15", "current_price": "0",
          "avg_entry_price": "0"}], [],
    )
    assert plan.legs == ()
    assert "ACN" in plan.skipped[0]


def test_it_falls_back_to_entry_price_when_the_mark_is_missing():
    plan = plan_extended_exit(
        [{"symbol": "ACN", "qty": "15", "current_price": None,
          "avg_entry_price": "99.00"}], [],
    )
    assert plan.legs[0].reference == 99.00


def test_only_filters_both_positions_and_orders():
    plan = plan_extended_exit(
        [_position("ACN", "15"), _position("PSX", "10")],
        [_order("abc", symbol="ACN"), _order("xyz", symbol="PSX")],
        only=frozenset({"PSX"}),
    )
    assert [leg.symbol for leg in plan.legs] == ["PSX"]
    assert plan.cancel_first == ("xyz",)


def test_legs_are_sorted_so_output_is_stable():
    plan = plan_extended_exit(
        [_position("PSX", "1"), _position("ACN", "1")], [],
    )
    assert [leg.symbol for leg in plan.legs] == ["ACN", "PSX"]


# --- the payload the broker will accept ---------------------------------

def test_payload_is_a_limit_order_flagged_for_extended_hours():
    """Both are mandatory. Alpaca rejects anything else outside regular
    hours, and a bracket is refused outright."""
    plan = plan_extended_exit([_position("ACN", "15")], [])
    payload = exit_payload(plan.legs[0])
    assert payload["type"] == "limit"
    assert payload["extended_hours"] is True
    assert payload["time_in_force"] == "day"
    assert "order_class" not in payload


def test_payload_never_carries_a_bracket_or_stop():
    plan = plan_extended_exit([_position("ACN", "15")], [])
    payload = exit_payload(plan.legs[0])
    assert not any(key in payload for key in
                   ("stop_loss", "take_profit", "stop_price", "trail_percent"))


def test_payload_quantity_is_a_whole_positive_number():
    plan = plan_extended_exit([_position("ACN", "-15")], [])
    payload = exit_payload(plan.legs[0])
    assert payload["qty"] == "15"
    assert payload["side"] == "buy"


def test_default_slippage_makes_a_sell_marketable():
    plan = plan_extended_exit([_position("ACN", "15", price="200.00")], [])
    assert plan.legs[0].limit < 200.00
    assert plan.legs[0].limit == marketable_limit(
        "sell", 200.00, DEFAULT_SLIPPAGE_BPS)
