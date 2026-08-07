"""Alpaca adapter tests.

Two properties matter more than the rest: paper must be the default, and the
same setup must never be able to open two positions. Everything else is
plumbing that fails loudly; those two fail silently and expensively.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from scanner.execution.alpaca import (
    LIVE_HOST,
    PAPER_HOST,
    Alpaca,
    BrokerError,
    bracket_payload,
    client_order_id,
    round_tick,
)
from scanner.execution.sizing import Sizing
from scanner.features.intraday import EXCHANGE_TZ
from scanner.screens.smc import Setup

AT = datetime(2026, 8, 5, 13, 30, tzinfo=EXCHANGE_TZ)
SETUP = Setup(
    symbol="MU", trigger_index=100, sweep_index=80, sweep_price=737.88,
    choch_index=90, choch_price=930.88, higher_low_index=95,
    higher_low_price=770.15, entry_price=893.19, stop_price=770.15,
    target_price=1011.77,
)
SIZING = Sizing(shares=12, risk_dollars=1476.0, position_dollars=10718.0,
                limited_by="risk budget")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


# ---- paper by default --------------------------------------------------

def test_paper_is_the_default_host():
    broker = Alpaca("key", "secret", _client())
    assert broker.host == PAPER_HOST
    assert broker.paper is True


def test_live_requires_saying_so():
    assert Alpaca("key", "secret", _client(), paper=False).host == LIVE_HOST


def test_missing_keys_fail_immediately():
    with pytest.raises(BrokerError):
        Alpaca("", "", _client())


def test_account_is_not_tradeable_when_not_active_or_suspended():
    from scanner.execution.alpaca import Account

    assert not Account(equity=100_000, cash=100_000, buying_power=100_000,
                       status="APPROVAL_PENDING").tradeable
    assert not Account(equity=100_000, cash=100_000, buying_power=100_000,
                       blocked=True).tradeable


# ---- the duplicate guard -----------------------------------------------

def test_the_same_bar_always_produces_the_same_order_id():
    """A restart or a repeated scan must not open a second position."""
    assert client_order_id(SETUP, AT) == client_order_id(SETUP, AT)


def test_a_different_bar_produces_a_different_id():
    assert client_order_id(SETUP, AT) != client_order_id(SETUP, AT + timedelta(minutes=30))


def test_a_different_symbol_produces_a_different_id():
    other = SETUP.model_copy(update={"symbol": "NVDA"})
    assert client_order_id(SETUP, AT) != client_order_id(other, AT)


def test_the_id_is_not_derived_from_the_wall_clock():
    """Deriving it from now() would give two ids for one bar, which is
    exactly the bug the id exists to prevent."""
    first = client_order_id(SETUP, AT)
    assert client_order_id(SETUP, AT) == first


# ---- the order body ----------------------------------------------------

def test_the_bracket_carries_all_three_prices():
    payload = bracket_payload(SETUP, SIZING, AT)
    assert payload["order_class"] == "bracket"
    assert payload["limit_price"] == "893.19"
    assert payload["stop_loss"]["stop_price"] == "770.15"
    assert payload["take_profit"]["limit_price"] == "1011.77"


def test_entry_is_a_limit_not_a_market_order():
    """The backtest assumed a fill at the trigger close. A market order
    fills wherever the book is and breaks that correspondence."""
    assert bracket_payload(SETUP, SIZING, AT)["type"] == "limit"


def test_quantity_comes_from_sizing():
    assert bracket_payload(SETUP, SIZING, AT)["qty"] == "12"


def test_extended_hours_is_off():
    """Extended hours rejects stop orders outright, so a bracket sent there
    would lose its protection."""
    assert bracket_payload(SETUP, SIZING, AT)["extended_hours"] is False


def test_prices_are_rounded_to_the_tick():
    assert round_tick(100.123456) == pytest.approx(100.12)
    assert round_tick(99.999) == pytest.approx(100.00)


def test_sub_penny_prices_never_reach_the_broker():
    odd = SETUP.model_copy(update={"entry_price": 12.34567, "stop_price": 11.11111,
                                   "target_price": 15.98765})
    payload = bracket_payload(odd, SIZING, AT)
    for price in (payload["limit_price"], payload["stop_loss"]["stop_price"],
                  payload["take_profit"]["limit_price"]):
        assert len(price.split(".")[1]) == 2
