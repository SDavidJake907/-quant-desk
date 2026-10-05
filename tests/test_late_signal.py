"""A setup first seen a scan late must not be bought into its own failure.

fresh_bars=2 lets a trigger one bar old still be acted on, at the OLD close as
the limit. When the price has since fallen, that buy limit sits above the
market and fills at once, next to the stop. Live, 2026-08-20 and 2026-09-24:

    APP  signal close 315.79, next close 312.38, sent 34 min late,
         filled 312.32 -- 0.50% above the stop, stopped 48 min later
    PSX  signal close 262.83, next close 260.37, sent 33 min late,
         filled 259.74 -- 0.57% above the stop, stopped 6 min later

Every on-time order filled within 0.34% of its limit; the only two fills more
than 1% under it are these two.
"""

from pathlib import Path

import pytest

from scanner.execution.guards import entry_drift_pct, late_signal
from scanner.execution.policy import TradingPolicy


def test_drift_is_how_far_below_the_entry_the_last_close_sits():
    assert entry_drift_pct(100.0, 99.0) == pytest.approx(1.0)
    assert entry_drift_pct(100.0, 101.0) == pytest.approx(-1.0)


def test_an_on_time_setup_has_no_drift():
    # The trigger bar IS the latest bar: its close is the entry.
    assert entry_drift_pct(315.79, 315.79) == 0.0
    assert late_signal(315.79, 315.79, 0.5).allowed


@pytest.mark.parametrize("symbol,entry,latest", [("APP", 315.79, 312.38),
                                                 ("PSX", 262.83, 260.37)])
def test_the_two_live_late_fills_would_have_been_refused(symbol, entry, latest):
    verdict = late_signal(entry, latest, 0.5)
    assert not verdict.allowed
    assert "below the entry" in verdict.reason


def test_a_price_that_moved_UP_is_not_refused():
    # The limit then rests below the market; the stale-entry cancel handles
    # it. Refusing it here would be a different rule.
    assert late_signal(100.0, 101.0, 0.5).allowed


def test_no_latest_close_does_not_block():
    assert late_signal(100.0, None, 0.5).allowed


def test_the_threshold_is_a_declared_policy_field():
    policy = TradingPolicy()
    assert policy.max_entry_drift_pct == 0.5
    assert policy.fingerprint != TradingPolicy(max_entry_drift_pct=1.0).fingerprint


def test_trade_py_applies_it_in_the_send_loop():
    source = (Path(__file__).parent.parent / "scripts" / "trade.py").read_text(encoding="utf-8")
    assert "late_signal(" in source
    assert "policy.max_entry_drift_pct" in source
    assert "latest_close[symbol] = bars.closes[-1]" in source
