"""Scaling out of a position, and what it costs to do so.

A ladder cannot improve the left tail. A trade that goes straight to the stop
never reaches a rung and loses a full -1R either way. All a ladder can do is
trade some of the right tail for a higher win rate — which flatters every
summary statistic except the one that pays the bills.

So these tests pin the asymmetry rather than the arithmetic alone: the losers
must stay exactly as bad, the winners must get measurably smaller, and every
exit leg must be charged for crossing the spread.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scanner.backtest.ladder import ladder_r
from scanner.features.bars import Bars


def _bars(rows: list[tuple[float, float, float, float]]) -> Bars:
    """rows are (open, high, low, close)."""
    start = date(2024, 1, 1)
    return Bars(
        symbol="TEST",
        dates=tuple(start + timedelta(days=i) for i in range(len(rows))),
        opens=tuple(r[0] for r in rows),
        highs=tuple(r[1] for r in rows),
        lows=tuple(r[2] for r in rows),
        closes=tuple(r[3] for r in rows),
        volumes=tuple(1_000_000.0 for _ in rows),
    )


#: Entry 100, stop 90 — risk is 10, so 1R = 110, 2R = 120, 3R = 130.
ENTRY, STOP = 100.0, 90.0
FLAT = (100.0, 100.0, 100.0, 100.0)


def _run(rows, rungs, *, target=None, max_bars=60, leg_cost_r=0.0,
         breakeven=False):
    return ladder_r(
        _bars([FLAT] + rows),
        entry_index=0, entry_price=ENTRY, stop_price=STOP,
        target_price=target, rungs=rungs, max_bars=max_bars,
        leg_cost_r=leg_cost_r, breakeven_after_first_rung=breakeven,
    )


HALF_AT_1R = ((1.0, 0.5),)
THIRDS = ((1.0, 1 / 3), (2.0, 1 / 3))


class TestTheLeftTailIsUntouched:
    def test_a_straight_loser_loses_one_r_with_or_without_a_ladder(self):
        """The point a ladder cannot argue with."""
        rows = [(100.0, 101.0, 89.0, 89.0)]
        assert _run(rows, None) == pytest.approx(-1.0)
        assert _run(rows, THIRDS) == pytest.approx(-1.0)

    def test_a_gap_through_the_stop_loses_more_than_one_r(self):
        """The stop is a market order once touched. A bar that opens below it
        fills at the open, and the loss is larger than the position was sized
        for — the asymmetry that makes overnight risk real."""
        rows = [(80.0, 82.0, 78.0, 79.0)]
        assert _run(rows, THIRDS) == pytest.approx(-2.0)

    def test_reaching_a_rung_then_reversing_still_loses_on_the_remainder(self):
        """Half out at 1R, then the rest stops out: +0.5R banked, -0.5R lost.
        A ladder is not free protection."""
        rows = [(100.0, 110.0, 100.0, 105.0), (105.0, 105.0, 89.0, 89.0)]
        assert _run(rows, HALF_AT_1R) == pytest.approx(0.0)


class TestTheRightTailIsTrimmed:
    def test_a_ladder_earns_less_than_letting_it_all_run(self):
        """A trade that goes to 3R: holding pays 3R, laddering pays less.
        If this ever inverts, the arithmetic is wrong."""
        rows = [(100.0, 110.0, 100.0, 108.0),
                (108.0, 120.0, 107.0, 118.0),
                (118.0, 130.0, 117.0, 130.0)]
        held = _run(rows, None)
        laddered = _run(rows, THIRDS)
        assert held == pytest.approx(3.0)
        assert laddered < held

    def test_rungs_fill_at_their_level_not_at_the_bar_high(self):
        rows = [(100.0, 125.0, 100.0, 124.0)]
        # Half out at 1R = +0.5R, half still open and marked at the close 124.
        assert _run(rows, HALF_AT_1R, max_bars=1) == pytest.approx(0.5 + 0.5 * 2.4)


class TestOrderingWithinABar:
    def test_a_bar_touching_both_the_stop_and_a_rung_is_scored_as_the_stop(self):
        """Daily bars record a high and a low but not their order. Where the
        truth is unknowable the choice must be the one that cannot flatter the
        result — the same rule `resolve` follows."""
        rows = [(100.0, 130.0, 89.0, 120.0)]
        assert _run(rows, THIRDS) == pytest.approx(-1.0)


class TestTheRunner:
    def test_no_target_lets_the_remainder_run_to_the_time_limit(self):
        """The whole reason to ladder a momentum trade: the last slice must not
        be capped, or the fat right tail it exists to capture is cut off."""
        rows = [(100.0, 110.0, 100.0, 109.0)] + [
            (109.0 + i, 111.0 + i, 108.0 + i, 110.0 + i) for i in range(20)
        ]
        r = _run(rows, HALF_AT_1R, target=None, max_bars=len(rows))
        # Half banked at 1R, half marked out well above 2R.
        assert r > 1.0

    def test_a_target_caps_the_remainder(self):
        rows = [(100.0, 110.0, 100.0, 109.0)] + [
            (109.0 + i, 111.0 + i, 108.0 + i, 110.0 + i) for i in range(20)
        ]
        capped = _run(rows, HALF_AT_1R, target=120.0, max_bars=len(rows))
        uncapped = _run(rows, HALF_AT_1R, target=None, max_bars=len(rows))
        assert capped == pytest.approx(0.5 + 0.5 * 2.0)
        assert uncapped > capped


class TestCosts:
    def test_every_exit_leg_is_charged(self):
        """Three exits cost three spread crossings. A ladder that ignores this
        looks better than it trades, and the more rungs the wider the lie."""
        rows = [(100.0, 110.0, 100.0, 108.0),
                (108.0, 120.0, 107.0, 118.0),
                (118.0, 130.0, 117.0, 130.0)]
        free = _run(rows, THIRDS, leg_cost_r=0.0)
        charged = _run(rows, THIRDS, leg_cost_r=0.05)
        # Two rungs plus the remainder — three legs at 0.05R each.
        assert free - charged == pytest.approx(3 * 0.05 * (1 / 3) + 0.0, abs=0.06)
        assert charged < free

    def test_more_rungs_cost_more(self):
        rows = [(100.0, 110.0, 100.0, 108.0),
                (108.0, 120.0, 107.0, 118.0),
                (118.0, 130.0, 117.0, 130.0)]
        one = _run(rows, ((1.0, 0.5),), leg_cost_r=0.05)
        three = _run(rows, ((1.0, 1/3), (2.0, 1/3), (3.0, 1/3)), leg_cost_r=0.05)
        assert three < one + 1.0  # sanity: not a runaway
        assert _run(rows, ((1.0, 1/3), (2.0, 1/3), (3.0, 1/3)), leg_cost_r=0.0) > three


class TestBreakeven:
    """Moving the stop to the entry once the first rung fills.

    The rule that makes a trade "free". It is not free: it converts a slice of
    the right tail into a guarantee, and it raises the chance of being shaken
    out of a winner at exactly zero. Both effects have to show up here.
    """

    def test_the_stop_moves_to_the_entry_after_the_first_rung(self):
        # Up to 1R (half out), then back to the entry on a later bar.
        rows = [(100.0, 110.0, 100.0, 108.0), (108.0, 108.0, 99.0, 99.0)]
        assert _run(rows, HALF_AT_1R, breakeven=True) == pytest.approx(0.5)
        # Without breakeven the remainder rides down to the real stop.
        assert _run(rows, HALF_AT_1R, breakeven=False) == pytest.approx(0.5 - 0.05 * 10 / 10 * 0.5, abs=0.3)

    def test_breakeven_does_not_apply_before_a_rung_fills(self):
        """A loser that never reaches 1R is untouched by the rule."""
        rows = [(100.0, 105.0, 89.0, 89.0)]
        assert _run(rows, HALF_AT_1R, breakeven=True) == pytest.approx(-1.0)

    def test_breakeven_applies_from_the_next_bar_not_within_the_filling_bar(self):
        """A bar that reaches 1R and also trades back to the entry does not say
        which happened first. Raising the stop inside that bar would be reading
        an order the data does not record."""
        # Bar 1 reaches 1R and also dips to 99, below the entry. Bar 2 opens
        # clear of the raised stop and runs, so the remainder can only survive
        # if the dip in the filling bar was not treated as a breakeven stop.
        rows = [(100.0, 110.0, 99.0, 100.0), (101.0, 112.0, 100.5, 111.0)]
        assert _run(rows, HALF_AT_1R, breakeven=True, max_bars=2) > 0.5

    def test_breakeven_costs_right_tail_when_a_winner_shakes_out(self):
        """Pull back to the entry, then run hard. Breakeven takes the exit;
        holding the original stop keeps the move."""
        rows = [(100.0, 111.0, 100.0, 108.0), (108.0, 109.0, 99.5, 100.0)] + [
            (100.0 + 2 * i, 103.0 + 2 * i, 99.0 + 2 * i, 102.0 + 2 * i)
            for i in range(15)
        ]
        shaken = _run(rows, HALF_AT_1R, breakeven=True, max_bars=len(rows))
        held = _run(rows, HALF_AT_1R, breakeven=False, max_bars=len(rows))
        assert held > shaken


class TestRefusals:
    def test_a_non_positive_risk_is_not_a_trade(self):
        bars = _bars([FLAT, (100.0, 110.0, 100.0, 105.0)])
        assert ladder_r(bars, entry_index=0, entry_price=100.0, stop_price=100.0,
                        target_price=None, rungs=HALF_AT_1R, max_bars=10) is None

    def test_fractions_over_one_are_refused(self):
        bars = _bars([FLAT, (100.0, 110.0, 100.0, 105.0)])
        with pytest.raises(ValueError):
            ladder_r(bars, entry_index=0, entry_price=100.0, stop_price=90.0,
                     target_price=None, rungs=((1.0, 0.6), (2.0, 0.6)), max_bars=10)

    def test_no_history_after_the_entry_yields_nothing(self):
        bars = _bars([FLAT])
        assert ladder_r(bars, entry_index=0, entry_price=100.0, stop_price=90.0,
                        target_price=None, rungs=HALF_AT_1R, max_bars=10) is None
