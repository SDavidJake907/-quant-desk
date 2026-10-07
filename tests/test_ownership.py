"""Ownership: the closers must only touch the strategy they were built for."""

from __future__ import annotations

import pytest

from scanner.execution import ownership as own


def buy(symbol, coid, filled_at="2026-10-06T14:00:00Z", **extra):
    return {"symbol": symbol, "side": "buy", "client_order_id": coid,
            "filled_at": filled_at, **extra}


def test_prefix_names_the_strategy():
    assert own.strategy_of("smc-NKE-1759770000") == own.SMC
    assert own.strategy_of("man-NVDA-1759770000") == own.MANUAL
    assert own.strategy_of("mom-SNDK-2026-10") == own.MOMENTUM
    assert own.strategy_of("8f2c-uuid") == own.UNKNOWN
    assert own.strategy_of(None) == own.UNKNOWN


def test_prefix_for_round_trips_and_refuses_unknown():
    for name in own.STRATEGIES:
        assert own.strategy_of(own.prefix_for(name) + "X") == name
    with pytest.raises(ValueError):
        own.prefix_for("crypto")


def test_newest_filled_buy_owns_the_symbol():
    orders = [
        buy("AMD", "smc-AMD-1", "2026-09-01T14:00:00Z"),
        buy("AMD", "man-AMD-2", "2026-10-01T14:00:00Z"),
        {"symbol": "AMD", "side": "sell", "client_order_id": "mom-AMD-x",
         "filled_at": "2026-10-05T14:00:00Z"},          # sells never decide
        {"symbol": "TSM", "side": "buy", "client_order_id": "mom-TSM",
         "filled_at": None},                              # unfilled never decides
    ]
    assert own.owners(orders) == {"AMD": own.MANUAL}


def test_bracket_legs_are_read_when_nested():
    parent = {"symbol": "NKE", "side": "sell", "client_order_id": "x",
              "filled_at": None,
              "legs": [buy("NKE", "smc-NKE-1")]}
    assert own.owners([parent]) == {"NKE": own.SMC}


def test_positions_for_keeps_only_named_strategies_and_never_unknown():
    owners = {"NKE": own.SMC, "NVDA": own.MANUAL, "SNDK": own.MOMENTUM}
    positions = [{"symbol": s} for s in ("NKE", "NVDA", "SNDK", "HANDMADE")]
    kept = own.positions_for(positions, owners, [own.SMC])
    assert [p["symbol"] for p in kept] == ["NKE"]
    both = own.positions_for(positions, owners, [own.SMC, own.MANUAL])
    assert [p["symbol"] for p in both] == ["NKE", "NVDA"]


def test_orders_for_keeps_own_entries_and_legs_on_owned_symbols():
    owners = {"NKE": own.SMC, "SNDK": own.MOMENTUM}
    open_orders = [
        {"id": "1", "symbol": "NKE", "client_order_id": "uuid-oco"},      # smc's stop
        {"id": "2", "symbol": "SNDK", "client_order_id": "uuid-oco"},     # momentum's
        {"id": "3", "symbol": "GS", "client_order_id": "smc-GS-9"},       # smc entry
        {"id": "4", "symbol": "NVDA", "client_order_id": "man-NVDA-9"},   # manual entry
        {"id": "5", "symbol": "NKE", "client_order_id": "man-NKE-9"},     # manual on smc name
    ]
    kept = own.orders_for(open_orders, owners, [own.SMC])
    assert [o["id"] for o in kept] == ["1", "3"]
