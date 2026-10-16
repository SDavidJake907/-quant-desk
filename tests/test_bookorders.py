"""Take-profit ladder tests.

Tests that Gemma's menu target choices (none/2R/3R/5R) and multi-target
ladders produce correct orders.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scanner.execution import bookorders
from scanner.execution.alpaca import (
    entry_payload, stop_payload, take_profit_payload,
)
from scanner.execution.policy import TradingPolicy
from scanner.models import TargetTier
from scanner.screens.smc import Setup


async def _fake_price_and_atr(symbol, client):
    """Return a fake last price and ATR for testing."""
    return 100.0, 2.0  # last=100, atr_pct=2.0


def _monkeypatch_price(monkeypatch):
    monkeypatch.setattr(bookorders, "price_and_atr", _fake_price_and_atr)


def _setup(limit: float = 100.0, stop: float = 95.0, target: float = 110.0,
           symbol: str = "TEST") -> Setup:
    return Setup(
        symbol=symbol, trigger_index=0, sweep_index=0, sweep_price=90.0,
        choch_index=1, choch_price=105.0, higher_low_index=2,
        higher_low_price=stop, entry_price=limit, stop_price=stop,
        target_price=target,
    )


def test_take_profit_payload():
    # 50 shares at target 110.0
    payload = take_profit_payload("TEST", 50, 110.0, "1R",
                                  datetime.now(timezone.utc), "smc-TEST-123")
    assert payload["symbol"] == "TEST"
    assert payload["qty"] == "50"
    assert payload["side"] == "sell"
    assert payload["type"] == "limit"
    assert float(payload["limit_price"]) == 110.0
    assert payload["reduce_only"] is True
    assert "tp1R" in payload["client_order_id"]


def test_stop_payload():
    payload = stop_payload("TEST", 100, 95.0, datetime.now(timezone.utc),
                           "smc-TEST-123")
    assert payload["symbol"] == "TEST"
    assert payload["qty"] == "100"
    assert payload["side"] == "sell"
    assert payload["type"] == "stop"
    assert float(payload["stop_price"]) == 95.0
    assert payload["reduce_only"] is True


def test_entry_payload():
    payload = entry_payload("TEST", 100, 100.0, datetime.now(timezone.utc),
                            "smc-TEST-123", entry_offset_pct=0.3)
    assert payload["symbol"] == "TEST"
    assert payload["qty"] == "100"
    assert payload["side"] == "buy"
    assert payload["type"] == "limit"
    assert float(payload["limit_price"]) == 100.30
    assert payload["time_in_force"] == "gtc"
    assert payload["client_order_id"] == "smc-TEST-123"


def test_multi_target_submit_all_legs(fake_broker, monkeypatch):
    """Multi-target setup submits entry + tier sells + stop as separate orders."""
    import asyncio

    _monkeypatch_price(monkeypatch)

    setup = _setup()
    setup = setup.__class__(
        symbol=setup.symbol, trigger_index=setup.trigger_index,
        sweep_index=setup.sweep_index, sweep_price=setup.sweep_price,
        choch_index=setup.choch_index, choch_price=setup.choch_price,
        higher_low_index=setup.higher_low_index,
        higher_low_price=setup.higher_low_price,
        entry_price=setup.entry_price, stop_price=setup.stop_price,
        target_price=setup.target_price,
        targets=(
            TargetTier(target_price=105.0, pct_of_shares=0.5, label="1R"),
            TargetTier(target_price=110.0, pct_of_shares=0.25, label="2R"),
            TargetTier(target_price=115.0, pct_of_shares=0.25, label="3R"),
        ),
    )

    async def run():
        return await bookorders.buy_with_stop(
            fake_broker, None, "TEST", strategy="smc", sleeve=40_000,
            room=40_000, cap_pct=12.5, policy=TradingPolicy(),
            reason="test", targets=setup.targets)

    res = asyncio.run(run())
    assert res.ok

    # Should have submitted: 1 entry + 3 tier sells + 1 stop = 5 orders
    assert len(fake_broker.submitted) == 5

    # First is the entry
    entry = fake_broker.submitted[0]
    assert entry["side"] == "buy"
    entry_qty = float(entry["qty"])
    assert entry_qty > 0

    # Next 3 are take-profit tiers
    tiers = fake_broker.submitted[1:4]
    tier_qtys = [float(t["qty"]) for t in tiers]
    assert tiers[0]["side"] == "sell"
    assert float(tiers[0]["limit_price"]) == 105.0
    assert tiers[1]["side"] == "sell"
    assert float(tiers[1]["limit_price"]) == 110.0
    assert tiers[2]["side"] == "sell"
    assert float(tiers[2]["limit_price"]) == 115.0

    # Tier quantities should be roughly pct * entry_qty
    assert abs(tier_qtys[0] - entry_qty * 0.5) < 2  # 50% of entry
    assert abs(tier_qtys[1] - entry_qty * 0.25) < 2  # 25% of entry
    assert abs(tier_qtys[2] - entry_qty * 0.25) < 2  # 25% of entry

    # Last is the stop for remaining shares
    stop_order = fake_broker.submitted[4]
    assert stop_order["side"] == "sell"
    assert stop_order["type"] == "stop"
    # stop = limit * (1 - atr_multiple * atr_pct / 100)
    # limit = 100 * 1.003 = 100.3, stop = 100.3 * 0.95 = 95.285
    assert abs(float(stop_order["stop_price"]) - 95.28) < 0.01
    assert stop_order["reduce_only"] is True


def test_single_target_still_uses_bracket(fake_broker, monkeypatch):
    """Single-target setups still use bracket_payload (one order)."""
    import asyncio

    _monkeypatch_price(monkeypatch)

    setup = _setup()

    async def run():
        return await bookorders.buy_with_stop(
            fake_broker, None, "TEST", strategy="smc", sleeve=40_000,
            room=40_000, cap_pct=12.5, policy=TradingPolicy(),
            reason="test")

    res = asyncio.run(run())
    assert res.ok
    assert len(fake_broker.submitted) == 1
    order = fake_broker.submitted[0]
    assert order["order_class"] == "bracket"
    assert "take_profit" in order
    assert "stop_loss" in order


def test_target_tier_validation():
    """pct_of_shares must be between 0 and 1."""
    tier = TargetTier(target_price=110.0, pct_of_shares=0.5, label="1R")
    assert 0.0 <= tier.pct_of_shares <= 1.0
