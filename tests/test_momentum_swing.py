"""Turning the trend screen into something with levels.

The screen ranks. It says nothing about where to get in, where to get out, or
how much to risk — so `sizing.size_position` cannot size it and
`backtest.setups.resolve` cannot score it. This module supplies exactly those
three numbers and nothing else.

THE DUPLICATE-SIGNAL TRAP, WHICH IS THE WHOLE REASON `detect` IS NOT A FILTER
A trend screen passes for as long as the trend lasts. Treat every passing bar
as a signal and one 60-day advance becomes 60 trades, all on the same move,
all resolving together. That inflates the trade count enormously, makes the
sample look far more significant than it is, and correlates every position in
the book. A signal is the bar where the screen STARTS passing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scanner.features.bars import Bars
from scanner.screens import momentum


def _bars(closes: list[float], symbol: str = "TEST", *, spread: float = 0.01) -> Bars:
    start = date(2020, 1, 1)
    return Bars(
        symbol=symbol,
        dates=tuple(start + timedelta(days=i) for i in range(len(closes))),
        opens=tuple(closes),
        closes=tuple(closes),
        volumes=tuple(1_000_000.0 for _ in closes),
        highs=tuple(c * (1 + spread) for c in closes),
        lows=tuple(c * (1 - spread) for c in closes),
    )


def _flat_benchmark(n: int = 800) -> Bars:
    """An index that goes nowhere, so relative strength is simply the stock's
    own 12-1 momentum. Keeps the RS criterion honest without making the test
    depend on a particular index path."""
    return _bars([100.0] * n, symbol="^BENCH", spread=0.0)


def _rising(n: int = 400, start: float = 50.0, step: float = 0.25) -> list[float]:
    """A clean advance: passes every trend criterion once history is deep."""
    return [start + step * i for i in range(n)]


def _flat_then_rising(flat: int = 300, rise: int = 120) -> list[float]:
    """Long dull base, then a breakout. The screen should fire once, at the
    point the advance actually establishes itself — not on every bar after."""
    return [50.0] * flat + [50.0 + 0.5 * i for i in range(rise)]


class TestLevels:
    def test_stop_sits_an_atr_multiple_below_the_entry(self):
        bars = _bars(_rising())
        setups = momentum.detect(bars, _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0)
        assert setups, "a clean uptrend should produce a setup"
        s = setups[0]
        atr = s.atr_pct / 100.0 * s.entry_price
        assert s.stop_price == pytest.approx(s.entry_price - 2.0 * atr, rel=1e-6)

    def test_target_is_the_reward_risk_multiple_of_the_stop_distance(self):
        bars = _bars(_rising())
        setups = momentum.detect(bars, _flat_benchmark(), atr_multiple=2.0, reward_risk=3.0)
        s = setups[0]
        assert s.reward_risk == pytest.approx(3.0, rel=1e-6)
        assert s.target_price == pytest.approx(
            s.entry_price + 3.0 * (s.entry_price - s.stop_price), rel=1e-6
        )

    def test_entry_is_the_close_of_the_signal_bar(self):
        """The same convention SMC measures, so the two strategies' R-multiples
        mean the same thing. An entry at the next open is a different trade and
        would need its own holdout."""
        bars = _bars(_rising())
        s = momentum.detect(bars, _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0)[0]
        assert s.entry_price == bars.closes[s.trigger_index]

    def test_a_wider_atr_multiple_gives_a_wider_stop(self):
        bars = _bars(_rising())
        tight = momentum.detect(bars, _flat_benchmark(), atr_multiple=1.0, reward_risk=2.0)[0]
        wide = momentum.detect(bars, _flat_benchmark(), atr_multiple=4.0, reward_risk=2.0)[0]
        assert wide.risk > tight.risk


class TestSignalIsATransition:
    def test_one_advance_produces_one_setup(self):
        """The trap this module exists to avoid. A screen that keeps passing
        must not keep firing."""
        bars = _bars(_rising(400))
        setups = momentum.detect(bars, _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0)
        assert len(setups) == 1, (
            f"{len(setups)} setups from a single uninterrupted advance — "
            "the screen is being read as a signal on every passing bar"
        )

    def test_the_signal_lands_on_the_bar_the_screen_starts_passing(self):
        bars = _bars(_flat_then_rising())
        setups = momentum.detect(bars, _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0)
        assert len(setups) == 1
        i = setups[0].trigger_index
        assert i >= 300, "signal fired during the flat base"
        # It passes at i and did not pass at i-1: that is what a transition is.
        assert momentum.passes(bars.upto(i), _flat_benchmark()) is True
        assert momentum.passes(bars.upto(i - 1), _flat_benchmark()) is False

    def test_a_trend_that_breaks_and_reforms_signals_twice(self):
        closes = _rising(340) + [125.0 - 1.5 * i for i in range(60)] + [
            35.0 + 0.9 * i for i in range(200)
        ]
        setups = momentum.detect(_bars(closes), _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0)
        assert len(setups) >= 2


class TestPointInTime:
    def test_features_never_see_bars_after_the_signal(self):
        """A lookahead here would be invisible in the result and fatal to it:
        the screen would be selecting on the advance it is about to trade."""
        closes = _flat_then_rising()
        full = momentum.detect(_bars(closes), _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0)
        assert full
        i = full[0].trigger_index

        # Truncating the series right after the signal must not move it.
        truncated = momentum.detect(
            _bars(closes[: i + 1]), _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0
        )
        assert truncated, "the signal vanished when the future was removed"
        assert truncated[-1].trigger_index == i
        assert truncated[-1].entry_price == pytest.approx(full[0].entry_price)


class TestRefusals:
    def test_short_history_yields_nothing(self):
        """The screen needs a 200-day MA and 12-1 momentum. Without them the
        honest answer is no setup, not a setup built on missing inputs."""
        assert momentum.detect(_bars(_rising(60)), _flat_benchmark(), atr_multiple=2.0,
                               reward_risk=2.0) == ()

    def test_a_downtrend_yields_nothing(self):
        falling = [200.0 - 0.3 * i for i in range(400)]
        assert momentum.detect(_bars(falling), _flat_benchmark(), atr_multiple=2.0,
                               reward_risk=2.0) == ()

    def test_zero_atr_cannot_produce_a_stop_at_the_entry(self):
        """A dead-flat series has no ATR. A zero-width stop would divide by
        zero in sizing and report an infinite R-multiple in the backtest."""
        flat = _bars([50.0] * 400, spread=0.0)
        for s in momentum.detect(flat, _flat_benchmark(), atr_multiple=2.0, reward_risk=2.0):
            assert s.stop_price < s.entry_price

    def test_stop_below_zero_is_refused(self):
        """A huge ATR multiple on a volatile name can push the stop negative,
        which is not a trade."""
        volatile = [50.0 + (10.0 if i % 2 else -10.0) for i in range(400)]
        for s in momentum.detect(_bars(volatile), _flat_benchmark(), atr_multiple=50.0,
                                 reward_risk=2.0):
            assert s.stop_price > 0


class TestCriteriaSubsets:
    """The screen is six criteria joined by AND. Nothing has ever asked which
    of them carry the result and which are dead weight — or worse, which are
    actively selecting for the wrong thing. `required` makes each one testable
    on its own, which is the prerequisite for finding out.
    """

    ALL_SIX = frozenset({
        "price > 50d MA", "50d > 150d > 200d", "200d MA rising",
        "within 25% of 52w high", "30%+ above 52w low", "outperforming index",
    })

    def test_requiring_every_criterion_matches_the_default(self):
        bars = _bars(_flat_then_rising())
        bench = _flat_benchmark()
        default = momentum.detect(bars, bench, atr_multiple=2.0, reward_risk=2.0)
        explicit = momentum.detect(bars, bench, atr_multiple=2.0, reward_risk=2.0,
                                   required=self.ALL_SIX)
        assert [s.trigger_index for s in default] == [s.trigger_index for s in explicit]

    def test_a_single_criterion_is_looser_than_all_six(self):
        """Dropping five of six ANDs cannot make the screen stricter."""
        bars = _bars(_flat_then_rising())
        bench = _flat_benchmark()
        strict = momentum.detect(bars, bench, atr_multiple=2.0, reward_risk=2.0)
        loose = momentum.detect(bars, bench, atr_multiple=2.0, reward_risk=2.0,
                                required=frozenset({"price > 50d MA"}))
        assert momentum.passes(bars.upto(len(bars) - 1), bench,
                               required=frozenset({"price > 50d MA"})) is True
        assert len(loose) >= len(strict)

    def test_an_unknown_criterion_name_is_refused(self):
        """A typo would silently drop a constraint and quietly loosen the
        screen — the failure mode that produces an unexplainable good result."""
        bars = _bars(_rising())
        with pytest.raises(ValueError):
            momentum.detect(bars, _flat_benchmark(), atr_multiple=2.0,
                            reward_risk=2.0, required=frozenset({"no such rule"}))

    def test_an_empty_requirement_is_refused(self):
        """Requiring nothing is not a screen; it would signal on bar 260 and
        never again, which is a bug that looks like a result."""
        bars = _bars(_rising())
        with pytest.raises(ValueError):
            momentum.detect(bars, _flat_benchmark(), atr_multiple=2.0,
                            reward_risk=2.0, required=frozenset())
