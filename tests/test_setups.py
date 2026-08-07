"""Trade-resolution tests.

These decide what the backtest reports, so they are the last line between an
honest result and a flattering one. The intrabar test is the important one:
resolving an ambiguous bar in the trade's favour is how an intraday backtest
invents an edge that does not survive contact with a broker.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from scanner.backtest.setups import (
    STOP,
    TARGET,
    TIMEOUT,
    Outcome,
    Report,
    random_control,
    resolve,
)
from scanner.features.intraday import EXCHANGE_TZ, IntradayBars
from scanner.screens.smc import Setup


def _bars(rows: list[tuple[float, float, float]]) -> IntradayBars:
    start = datetime(2026, 1, 5, 9, 30, tzinfo=EXCHANGE_TZ)
    closes = [r[2] for r in rows]
    return IntradayBars(
        symbol="TEST", interval_minutes=240, alignment="session",
        times=tuple(start + timedelta(hours=4 * i) for i in range(len(rows))),
        opens=tuple([closes[0]] + closes[:-1]),
        highs=tuple(r[0] for r in rows), lows=tuple(r[1] for r in rows),
        closes=tuple(r[2] for r in rows), volumes=tuple(1.0 for _ in rows),
    )


def _resolve(bars, **kw):
    base = dict(symbol="TEST", entry_index=0, entry_price=100.0,
                stop_price=90.0, target_price=120.0, max_bars=10)
    return resolve(bars, **{**base, **kw})


# ---- the ambiguous bar -------------------------------------------------

def test_a_bar_touching_both_stop_and_target_is_a_loss():
    """Bars record a high and a low but not their order. Resolving this in
    the trade's favour turns every whipsaw into a winner — the commonest way
    an intraday backtest manufactures an edge."""
    bars = _bars([(100, 100, 100), (125, 85, 110)])   # touches 120 and 90
    outcome = _resolve(bars)
    assert outcome.reason == STOP
    assert outcome.r_multiple == pytest.approx(-1.0)


def test_target_alone_is_a_win():
    bars = _bars([(100, 100, 100), (125, 95, 120)])
    outcome = _resolve(bars)
    assert outcome.reason == TARGET
    assert outcome.r_multiple == pytest.approx(2.0)


def test_stop_alone_is_a_one_r_loss():
    bars = _bars([(100, 100, 100), (105, 85, 88)])
    assert _resolve(bars).r_multiple == pytest.approx(-1.0)


def test_touching_the_level_exactly_counts():
    """A stop resting at 90 fills when the bar trades 90."""
    bars = _bars([(100, 100, 100), (105, 90, 95)])
    assert _resolve(bars).reason == STOP


# ---- time limit --------------------------------------------------------

def test_a_trade_that_goes_nowhere_exits_at_the_time_limit():
    rows = [(100, 100, 100)] + [(105, 95, 101)] * 12
    outcome = _resolve(_bars(rows), max_bars=5)
    assert outcome.reason == TIMEOUT
    assert outcome.exit_index == 5
    assert outcome.r_multiple == pytest.approx(0.1)


def test_an_unresolved_trade_is_dropped_not_scored():
    """Counting a still-open trade as flat would invent a result. The last
    setups in any series are exactly the ones with no room left to resolve."""
    rows = [(100, 100, 100)] + [(105, 95, 101)] * 3
    assert _resolve(_bars(rows), max_bars=20) is None


def test_trade_crossing_holdout_boundary_is_dropped():
    rows = [(105, 95, 101)] * 12
    bars = _bars(rows)
    assert _resolve(bars, max_bars=10, end_index=5) is None


def test_resolve_setups_can_select_only_holdout_entries():
    rows = [(105, 95, 101)] * 20
    setup = _setup(trigger=12)
    from scanner.backtest.setups import resolve_setups

    outcomes = resolve_setups(
        _bars(rows), (setup,), max_bars=3, start_index=10, end_index=20
    )
    assert len(outcomes) == 1
    assert outcomes[0].entry_index == 12


# ---- input sanity ------------------------------------------------------

def test_a_stop_above_entry_is_rejected():
    bars = _bars([(100, 100, 100), (125, 85, 110)])
    assert _resolve(bars, stop_price=110.0) is None


def test_a_target_below_entry_is_rejected():
    bars = _bars([(100, 100, 100), (125, 85, 110)])
    assert _resolve(bars, target_price=95.0) is None


def test_r_multiple_is_scale_free():
    """A $9 stock and a $900 stock have to be comparable, or the average is
    just a weighting of share prices."""
    cheap = Outcome(symbol="A", entry_index=0, entry_price=10.0, stop_price=9.0,
                    target_price=12.0, exit_index=1, exit_price=12.0, reason=TARGET)
    dear = Outcome(symbol="B", entry_index=0, entry_price=1000.0, stop_price=900.0,
                   target_price=1200.0, exit_index=1, exit_price=1200.0, reason=TARGET)
    assert cheap.r_multiple == pytest.approx(dear.r_multiple)


# ---- the control -------------------------------------------------------

def _setup(entry=100.0, stop=90.0, target=120.0, trigger=5) -> Setup:
    return Setup(
        symbol="TEST", trigger_index=trigger, sweep_index=0, sweep_price=80.0,
        choch_index=2, choch_price=110.0, higher_low_index=4,
        higher_low_price=stop, entry_price=entry, stop_price=stop,
        target_price=target,
    )


def test_control_copies_the_stop_and_target_distances():
    """The control must be this strategy with the pattern removed, not a
    different strategy — otherwise it measures the wrong thing."""
    rows = [(100 + i, 90 + i, 95 + i) for i in range(60)]
    outcomes = random_control(_bars(rows), (_setup(),), random.Random(1), max_bars=5)
    for o in outcomes:
        assert o.stop_price / o.entry_price == pytest.approx(0.9)
        assert o.target_price / o.entry_price == pytest.approx(1.2)


def test_control_is_reproducible_from_its_seed():
    rows = [(100 + i, 90 + i, 95 + i) for i in range(60)]
    a = random_control(_bars(rows), (_setup(),) * 5, random.Random(42), max_bars=5)
    b = random_control(_bars(rows), (_setup(),) * 5, random.Random(42), max_bars=5)
    assert [o.entry_index for o in a] == [o.entry_index for o in b]


def test_control_on_a_series_too_short_returns_nothing():
    assert random_control(_bars([(1, 1, 1)] * 3), (_setup(),), random.Random(0)) == ()


# ---- reporting ---------------------------------------------------------

def _outcome(r: float) -> Outcome:
    return Outcome(symbol="X", entry_index=0, entry_price=100.0, stop_price=90.0,
                   target_price=120.0, exit_index=1,
                   exit_price=100.0 + r * 10.0, reason=TARGET if r > 0 else STOP)


def test_expectancy_is_mean_r_not_win_rate():
    """A 30% win rate at +3R beats a 70% win rate at -1R, and only expectancy
    says so."""
    report = Report(label="x", outcomes=tuple(_outcome(r) for r in (3, -1, -1, 3, -1)))
    assert report.win_rate == pytest.approx(40.0)
    assert report.expectancy == pytest.approx(0.6)


def test_empty_report_is_safe():
    empty = Report(label="none", outcomes=())
    assert empty.count == 0
    assert empty.expectancy == 0.0
    assert empty.win_rate == 0.0
    assert empty.t_stat == 0.0


def test_t_stat_needs_more_than_one_trade():
    assert Report(label="x", outcomes=(_outcome(1.0),)).t_stat == 0.0


# ---- gaps ---------------------------------------------------------------
#
# A stop is a market order once triggered, so a bar that OPENS below it fills
# at the open and loses more than the 1R the position was sized for. A target
# is a resting limit, so a gap through it fills better. The asymmetry is the
# whole reason gaps matter, and the old model excluded it entirely.

def _gap_bars(rows):
    """rows: (open, high, low, close)"""
    start = datetime(2026, 1, 5, 9, 30, tzinfo=EXCHANGE_TZ)
    return IntradayBars(
        symbol="TEST", interval_minutes=240, alignment="session",
        times=tuple(start + timedelta(hours=4 * i) for i in range(len(rows))),
        opens=tuple(r[0] for r in rows), highs=tuple(r[1] for r in rows),
        lows=tuple(r[2] for r in rows), closes=tuple(r[3] for r in rows),
        volumes=tuple(1.0 for _ in rows),
    )


def test_a_gap_through_the_stop_loses_more_than_one_r():
    """The failure the old model could not represent: the position was sized
    for a 1R loss and took 2R because the stop never traded."""
    bars = _gap_bars([(100, 100, 100, 100), (80, 82, 78, 79)])
    outcome = resolve(bars, symbol="T", entry_index=0, entry_price=100.0,
                      stop_price=90.0, target_price=120.0, max_bars=10)
    assert outcome.reason == STOP
    assert outcome.gapped
    assert outcome.exit_price == pytest.approx(80.0)   # the open, not the stop
    assert outcome.r_multiple == pytest.approx(-2.0)


def test_a_gap_through_the_target_fills_better_than_planned():
    """A resting limit takes any better price, so this one is a windfall."""
    bars = _gap_bars([(100, 100, 100, 100), (130, 132, 128, 131)])
    outcome = resolve(bars, symbol="T", entry_index=0, entry_price=100.0,
                      stop_price=90.0, target_price=120.0, max_bars=10)
    assert outcome.reason == TARGET
    assert outcome.gapped
    assert outcome.exit_price == pytest.approx(130.0)
    assert outcome.r_multiple == pytest.approx(3.0)


def test_an_ordinary_intrabar_touch_still_fills_at_the_level():
    bars = _gap_bars([(100, 100, 100, 100), (99, 105, 85, 95)])
    outcome = resolve(bars, symbol="T", entry_index=0, entry_price=100.0,
                      stop_price=90.0, target_price=120.0, max_bars=10)
    assert not outcome.gapped
    assert outcome.exit_price == pytest.approx(90.0)
    assert outcome.r_multiple == pytest.approx(-1.0)


def test_a_gap_open_beats_the_intrabar_rule():
    """If the bar opened below the stop it never traded at the target that
    session, however high the bar later reached."""
    bars = _gap_bars([(100, 100, 100, 100), (85, 125, 84, 124)])
    outcome = resolve(bars, symbol="T", entry_index=0, entry_price=100.0,
                      stop_price=90.0, target_price=120.0, max_bars=10)
    assert outcome.reason == STOP
    assert outcome.exit_price == pytest.approx(85.0)


def test_gap_fills_can_be_disabled_to_measure_what_they_cost():
    bars = _gap_bars([(100, 100, 100, 100), (80, 82, 78, 79)])
    old = resolve(bars, symbol="T", entry_index=0, entry_price=100.0,
                  stop_price=90.0, target_price=120.0, max_bars=10,
                  gap_fills=False)
    assert old.r_multiple == pytest.approx(-1.0)
    assert not old.gapped


def test_gap_cost_counts_stops_only():
    """A gapped target is a windfall, not a cost, and netting the two would
    hide how much the losses actually widened."""
    good = Outcome(symbol="A", entry_index=0, entry_price=100.0, stop_price=90.0,
                   target_price=120.0, exit_index=1, exit_price=130.0,
                   reason=TARGET, gapped=True)
    bad = Outcome(symbol="B", entry_index=0, entry_price=100.0, stop_price=90.0,
                  target_price=120.0, exit_index=1, exit_price=80.0,
                  reason=STOP, gapped=True)
    report = Report(label="x", outcomes=(good, bad))
    assert report.gapped == 2
    assert report.gap_cost_r == pytest.approx(-1.0)
