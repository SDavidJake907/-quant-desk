"""Momentum paper orders: never share a position, never double an order."""

from __future__ import annotations

from datetime import date

import pytest

from scanner.execution.ownership import MOMENTUM, strategy_of
from scanner.momentum import paper

DAY = date(2026, 11, 2)


def test_picks_held_by_another_strategy_are_dropped_in_rank_order():
    assert paper.drop_foreign(("SNDK", "MU", "LITE", "WDC"), {"LITE", "AMD"}) == (
        "SNDK", "MU", "WDC")


def test_client_id_is_stable_within_a_month_and_owned_by_momentum():
    a = paper.client_order_id("MU", "buy", DAY)
    b = paper.client_order_id("MU", "buy", date(2026, 11, 3))
    assert a == b == "mom-MU-202611-buy"
    assert strategy_of(a) == MOMENTUM
    assert a != paper.client_order_id("MU", "sell", DAY)


def test_moc_payload_shape():
    body = paper.moc_payload("MU", "buy", 12, DAY)
    assert body == {"symbol": "MU", "qty": "12", "side": "buy", "type": "market",
                    "time_in_force": "cls", "client_order_id": "mom-MU-202611-buy"}


@pytest.mark.parametrize("side,qty", [("short", 1), ("buy", 0)])
def test_moc_payload_refuses_nonsense(side, qty):
    with pytest.raises(ValueError):
        paper.moc_payload("MU", side, qty, DAY)


def test_sells_are_clamped_to_what_is_held():
    trades = (("A", "sell", 10), ("B", "sell", 5), ("C", "buy", 3))
    assert paper.clamp_sells(trades, {"A": 4}) == (("A", "sell", 4), ("C", "buy", 3))


def test_duplicate_refusal_is_recognised():
    assert paper.is_duplicate('rejected: {"message":"client_order_id must be unique"}')
    assert not paper.is_duplicate("insufficient buying power")
