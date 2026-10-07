"""Manual picks: refused loudly when wrong, shaped exactly like the scanner's."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scanner.execution import manual
from scanner.execution.ownership import MANUAL, strategy_of

AT = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,want", [("nvda", "NVDA"), (" BRK.B ", "BRK.B")])
def test_symbols_are_normalised(raw, want):
    assert manual.validate_symbol(raw) == want


@pytest.mark.parametrize("raw", ["BTC-USD", "BTC/USD", "", "TOOLONG", "AAPL240119C"])
def test_non_stock_symbols_are_refused(raw):
    with pytest.raises(manual.ManualError):
        manual.validate_symbol(raw)


def test_target_defaults_to_reward_risk_multiple():
    stop, target = manual.resolve_levels(100.0, 95.0, None, 2.0)
    assert (stop, target) == (95.0, 110.0)


def test_explicit_target_wins():
    assert manual.resolve_levels(100.0, 95.0, 120.0, 2.0) == (95.0, 120.0)


@pytest.mark.parametrize("entry,stop,target", [
    (100.0, 100.0, 110.0),   # stop not below entry
    (100.0, 101.0, 110.0),
    (100.0, 95.0, 99.0),     # target not above entry
    (0.0, 95.0, 110.0),
])
def test_bad_levels_are_refused(entry, stop, target):
    with pytest.raises(manual.ManualError):
        manual.resolve_levels(entry, stop, target, 2.0)


def test_limit_carries_the_scanner_cushion():
    assert manual.limit_price(100.0, 0.3) == 100.30


def test_sleeve_is_what_momentum_does_not_claim():
    assert manual.sleeve_equity(100_000, 50.0) == 50_000
    assert manual.sleeve_equity(100_000, 0.0) == 100_000
    assert manual.sleeve_equity(100_000, 150.0) == 0.0


def test_sector_cap_refuses_the_fourth_chip():
    sector_of = {"AMD": "IT", "MRVL": "IT", "TSM": "IT", "GS": "Financials"}
    counts = manual.sector_counts(["AMD", "MRVL", "TSM", "GS", "XYZ"], sector_of)
    assert counts == {"IT": 3, "Financials": 1}
    assert not manual.check_sector("IT", counts, 3)
    assert manual.check_sector("Financials", counts, 3)
    assert manual.check_sector("IT", counts, 0)          # disabled
    assert manual.check_sector("", counts, 3)            # unknown is allowed


def test_bracket_is_gtc_with_a_manual_id():
    body = manual.bracket("NVDA", 10, limit=100.30, stop=95.0, target=110.0, at=AT)
    assert body["order_class"] == "bracket"
    assert body["time_in_force"] == "gtc"
    assert body["stop_loss"] == {"stop_price": "95.00"}
    assert body["take_profit"] == {"limit_price": "110.00"}
    assert strategy_of(body["client_order_id"]) == MANUAL


@pytest.mark.parametrize("shares,limit,stop,target", [
    (0, 100.0, 95.0, 110.0),
    (10, 94.0, 95.0, 110.0),     # limit under the stop: would fill and stop at once
    (10, 111.0, 95.0, 110.0),
])
def test_bracket_refuses_impossible_orders(shares, limit, stop, target):
    with pytest.raises(manual.ManualError):
        manual.bracket("NVDA", shares, limit=limit, stop=stop, target=target, at=AT)
