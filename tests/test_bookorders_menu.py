"""Gemma's menu choices become a stop distance, a target and a risk size."""

from __future__ import annotations

import pytest

from scanner.execution import bookorders
from scanner.execution.policy import TradingPolicy


def test_target_from_reward_risk():
    assert bookorders.target_price(limit=100.0, stop=90.0, reward_risk=3.0, ceiling=2.0) == 130.0


def test_no_target_is_the_ceiling():
    assert bookorders.target_price(limit=100.0, stop=90.0, reward_risk=0.0, ceiling=2.0) == 200.0


def test_policy_menus_resolve_by_name():
    p = TradingPolicy()
    # One choice each from 2026-10-26 (the core-and-satellite plan): the rules exit and size.
    assert p.gemma_menu_value("stop", "normal") == 2.5
    assert p.gemma_menu_value("target", "none") == 0.0
    assert p.gemma_menu_value("size", "normal") == p.gemma_risk_pct
    with pytest.raises(KeyError):
        p.gemma_menu_value("target", "5R")
    with pytest.raises(KeyError):
        p.gemma_menu_value("stop", "yolo")
