"""The academic momentum factor: a ranking, not a trigger.

This is the distinction the swing experiment got wrong. Jegadeesh and Titman
rank a universe by trailing return and hold the top slice for months. There is
no entry condition, no stop and no target -- the position exists because the
name is near the top of a list, and it leaves when it is not.

So the only jobs here are: score by 12-1 momentum, and refuse to rank a name
whose momentum cannot be computed. Everything else belongs to the engine.
"""

from __future__ import annotations

from datetime import date, timedelta

from scanner.features.bars import Bars
from scanner.features.technicals import compute
from scanner.screens import momentum_factor


def _bars(closes: list[float], symbol: str = "TEST") -> Bars:
    start = date(2020, 1, 1)
    return Bars(
        symbol=symbol,
        dates=tuple(start + timedelta(days=i) for i in range(len(closes))),
        opens=tuple(closes),
        closes=tuple(closes),
        volumes=tuple(1_000_000.0 for _ in closes),
        highs=tuple(c * 1.01 for c in closes),
        lows=tuple(c * 0.99 for c in closes),
    )


def _features(closes: list[float], symbol: str = "TEST"):
    return compute(_bars(closes, symbol))


def _rising(n: int, step: float, start: float = 100.0) -> list[float]:
    return [start + step * i for i in range(n)]


class TestScoring:
    def test_score_is_the_12_1_momentum(self):
        f = _features(_rising(400, 0.5))
        c = momentum_factor.evaluate(f, None)
        assert c.score == f.momentum_12_1

    def test_a_stronger_riser_outranks_a_weaker_one(self):
        strong = momentum_factor.evaluate(_features(_rising(400, 1.0), "STRONG"), None)
        weak = momentum_factor.evaluate(_features(_rising(400, 0.1), "WEAK"), None)
        assert strong.score > weak.score

    def test_a_faller_scores_below_a_riser(self):
        up = momentum_factor.evaluate(_features(_rising(400, 0.5), "UP"), None)
        down = momentum_factor.evaluate(
            _features([300.0 - 0.4 * i for i in range(400)], "DOWN"), None)
        assert down.score < up.score

    def test_ranking_puts_the_strongest_first(self):
        cands = tuple(
            momentum_factor.evaluate(_features(_rising(400, step), name), None)
            for name, step in (("SLOW", 0.1), ("FAST", 1.0), ("MID", 0.4))
        )
        from scanner.screens.trend import rank
        assert [c.symbol for c in rank(cands)] == ["FAST", "MID", "SLOW"]


class TestRefusals:
    def test_a_name_without_12_1_momentum_does_not_pass(self):
        """Under a year of history there is no 12-1 return. Such a name must
        not be ranked at all -- scoring it as zero would place it above every
        genuine decliner and quietly fill the book with new listings."""
        c = momentum_factor.evaluate(_features(_rising(100, 0.5)), None)
        assert c.passed_all is False

    def test_a_name_with_momentum_passes(self):
        c = momentum_factor.evaluate(_features(_rising(400, 0.5)), None)
        assert c.passed_all is True

    def test_an_unrankable_name_sorts_last_not_first(self):
        from scanner.screens.trend import rank
        short = momentum_factor.evaluate(_features(_rising(100, 0.5), "SHORT"), None)
        faller = momentum_factor.evaluate(
            _features([300.0 - 0.4 * i for i in range(400)], "FALLER"), None)
        ordered = rank((short, faller), require_all=False)
        assert ordered[-1].symbol == "SHORT"


class TestItIsOnlyARanking:
    def test_the_screen_states_no_entry_stop_or_target(self):
        """A guard against this drifting back into a timing signal. The factor
        has no levels; adding any here would recreate the swing experiment
        that lost to random entries in 38 of 40 configurations."""
        c = momentum_factor.evaluate(_features(_rising(400, 0.5)), None)
        for forbidden in ("entry_price", "stop_price", "target_price"):
            assert not hasattr(c, forbidden)
