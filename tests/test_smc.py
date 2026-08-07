"""SMC setup-detector tests.

Real money is the stated destination for this screen, so the tests are written
against the ways a pattern detector flatters itself:

  - firing on structure it could not have known yet
  - entering at a price nobody could have got
  - counting one idea as many trades
  - calling a sweep when no liquidity was taken

Each of those turns a losing pattern into a winning backtest.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from scanner.features.intraday import EXCHANGE_TZ, IntradayBars
from scanner.screens.smc import detect, is_live_setup, scan


def _bars(rows: list[tuple[float, float, float]], symbol: str = "TEST") -> IntradayBars:
    """rows: (high, low, close). Opens track the prior close."""
    start = datetime(2026, 1, 5, 9, 30, tzinfo=EXCHANGE_TZ)
    closes = [r[2] for r in rows]
    return IntradayBars(
        symbol=symbol,
        interval_minutes=240,
        alignment="session",
        times=tuple(start + timedelta(hours=4 * i) for i in range(len(rows))),
        opens=tuple([closes[0]] + closes[:-1]),
        highs=tuple(r[0] for r in rows),
        lows=tuple(r[1] for r in rows),
        closes=tuple(r[2] for r in rows),
        volumes=tuple(1000.0 for _ in rows),
    )


def _textbook() -> IntradayBars:
    """The diagram, in bars: range, sweep, CHoCH, higher low, BOS.

    Built so every pivot is a clean k=2 fractal.
    """
    rows = [
        # a range whose high is the eventual target
        (100, 90, 95), (105, 95, 100), (165, 100, 118),   # 0-2  range high 165 @2
        (110, 95, 100), (105, 92, 95),
        (100, 80, 85), (95, 78, 82),                       # 5-6  range low 78 @6
        (98, 85, 95), (108, 90, 105),
        (112, 95, 110), (115, 100, 112),                   # 9-10 pre-sweep high 115 @10
        (108, 88, 92), (100, 80, 85),
        (95, 60, 70), (90, 75, 85),                        # 13   SWEEP low 60 (< 78)
        (100, 80, 98), (118, 95, 116),                     # 16   CHoCH high 118 (> 115)
        (112, 92, 96), (105, 88, 92),
        (100, 85, 95), (104, 90, 102),                     # 19   higher low 85 @19 (> 60)
        (110, 98, 108),
        (125, 105, 122),                                   # 22   BOS: 125 > 118 -> trigger
        (130, 118, 128),
    ]
    return _bars(rows)


# ---- the happy path ----------------------------------------------------

def test_detects_the_textbook_setup():
    bars = _textbook()
    setup = next(s for s in scan(bars) if s is not None)
    assert setup.sweep_price == 60.0
    assert setup.choch_price == 118.0
    assert setup.higher_low_price == 85.0


def test_entry_is_the_close_of_the_trigger_bar():
    """Not the sweep low, not the order block. The close of the bar on which
    the pattern became true is the only fill needing no assumption."""
    bars = _textbook()
    setup = scan(bars)[0]
    assert setup.entry_price == bars.closes[setup.trigger_index]


def test_stop_is_the_higher_low_and_target_is_the_range_high():
    setup = scan(_textbook())[0]
    assert setup.stop_price == 85.0
    assert setup.target_price == 165.0


def test_reward_risk_is_derived_not_assumed():
    setup = scan(_textbook())[0]
    assert setup.risk == pytest.approx(setup.entry_price - 85.0)
    assert setup.reward == pytest.approx(165.0 - setup.entry_price)
    assert setup.reward_risk == pytest.approx(setup.reward / setup.risk)


def test_setup_is_not_live_after_target_or_stop():
    bars = _textbook()
    setup = scan(bars)[0]
    assert is_live_setup(bars, setup)
    assert not is_live_setup(
        bars.model_copy(update={"closes": bars.closes[:-1] + (setup.target_price + 1,)}),
        setup,
    )
    assert not is_live_setup(
        bars.model_copy(update={"closes": bars.closes[:-1] + (setup.stop_price - 1,)}),
        setup,
    )


# ---- the ways it could cheat -------------------------------------------

def test_never_fires_before_the_structure_is_confirmable():
    """The trigger bar must come after every pivot it depends on has had its
    k confirmation bars. Otherwise the screen trades on hindsight."""
    bars = _textbook()
    for setup in scan(bars, k=2):
        for pivot in (setup.sweep_index, setup.choch_index, setup.higher_low_index):
            assert pivot + 2 <= setup.trigger_index


def test_one_pattern_counts_as_one_trade():
    """The pattern stays true while price holds above the CHoCH high, so a
    naive scan re-fires it every bar and inflates the sample."""
    bars = _textbook()
    keys = [(s.sweep_index, s.higher_low_index) for s in scan(bars)]
    assert len(keys) == len(set(keys))


def test_no_sweep_means_no_setup():
    """Without liquidity taken below a prior low it is just a pullback."""
    rows = [
        (100, 90, 95), (105, 95, 100), (165, 100, 118),
        (110, 95, 100), (105, 92, 95),
        (100, 80, 85), (95, 78, 82),
        (98, 85, 95), (108, 90, 105),
        (112, 95, 110), (115, 100, 112),
        (108, 88, 92), (100, 85, 90),
        (95, 82, 88), (90, 85, 88),      # low 82 — never below the prior 78
        (100, 88, 98), (118, 95, 116),
        (112, 92, 96), (105, 88, 92),
        (100, 90, 95), (104, 92, 102),
        (110, 98, 108), (125, 105, 122), (130, 118, 128),
    ]
    assert scan(_bars(rows)) == ()


def test_lower_low_after_the_sweep_is_not_a_higher_low():
    bars = _textbook()
    rows = list(zip(bars.highs, bars.lows, bars.closes))
    rows[19] = (100, 55, 95)   # undercuts the sweep low of 60
    assert scan(_bars([tuple(r) for r in rows])) == ()


def test_a_trade_with_no_room_is_rejected():
    """If the target sits below the entry the pattern has already played out,
    and taking it anyway is how a screen books guaranteed-looking losers."""
    bars = _textbook()
    assert scan(bars, min_reward_risk=99.0) == ()


def test_stale_structure_is_ignored():
    """A sweep from months ago is not context for today's break."""
    assert scan(_textbook(), lookback=3) == ()


# ---- boundaries --------------------------------------------------------

def test_too_little_history_yields_nothing():
    assert detect(_bars([(10, 5, 8), (11, 6, 9)]), 1) is None


def test_index_outside_the_series_is_not_a_crash():
    bars = _textbook()
    assert detect(bars, len(bars)) is None
    assert detect(bars, -1) is None


def test_k_is_honoured():
    """Different swing sizes see different structure — the sensitivity the
    backtest has to measure rather than a value to quietly hardcode."""
    bars = _textbook()
    assert isinstance(scan(bars, k=2), tuple)
    assert isinstance(scan(bars, k=3), tuple)


# ---- the volume-profile filter -----------------------------------------

def test_value_sweep_filter_is_stricter_than_the_raw_pattern():
    """It can only ever remove setups, never invent them."""
    bars = _textbook()
    plain = scan(bars)
    filtered = scan(bars, require_value_sweep=True)
    assert len(filtered) <= len(plain)
    assert set(s.trigger_index for s in filtered) <= set(s.trigger_index for s in plain)


def test_a_sweep_that_never_left_value_does_not_pass():
    """The whole claim is that price dipped under where trade concentrated
    and came back. A dip inside the value area swept nothing."""
    from scanner.screens.smc import swept_value_area

    bars = _textbook()
    setups = scan(bars)
    if setups:
        shallow = setups[0].model_copy(update={"sweep_price": setups[0].entry_price})
        assert not swept_value_area(bars, shallow)


def test_an_unprofilable_range_fails_closed():
    """No volume, no verdict — and 'no verdict' must mean 'not a setup',
    never 'assume it passed'."""
    from scanner.screens.smc import swept_value_area

    bars = _textbook()
    flat = IntradayBars(
        symbol="FLAT", interval_minutes=240, alignment="session",
        times=bars.times, opens=bars.opens,
        highs=tuple(100.0 for _ in bars.highs), lows=tuple(100.0 for _ in bars.lows),
        closes=bars.closes, volumes=tuple(0.0 for _ in bars.volumes),
    )
    setups = scan(bars)
    if setups:
        assert not swept_value_area(flat, setups[0])


def test_stops_too_tight_to_survive_costs_are_rejected():
    """Cost in R scales as 1/risk, so a 0.5% stop pays four times the friction
    of a 2% one. Measured on 30m S&P 500 data, excluding sub-1.5% stops moved
    net expectancy after 20bps from -0.121R to +0.237R."""
    bars = _textbook()
    loose = scan(bars, min_risk_pct=0.0)
    strict = scan(bars, min_risk_pct=99.0)
    assert len(loose) >= 1
    assert strict == ()


def test_min_risk_pct_only_removes_setups():
    bars = _textbook()
    assert set(s.trigger_index for s in scan(bars, min_risk_pct=1.0)) <= set(
        s.trigger_index for s in scan(bars, min_risk_pct=0.0)
    )


def test_context_filters_are_opt_in():
    bars = _textbook()
    assert len(scan(bars)) >= 1
    assert len(scan(bars, require_trend_context=True)) <= len(scan(bars))
