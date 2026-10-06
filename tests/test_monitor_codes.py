"""Monitor v2: the trade math, computed in Python, shown to Gemma as short codes.

Gemma reads numbers it did not compute and letters that reveal nothing on a
lock screen. These tests pin the arithmetic.
"""

from __future__ import annotations

import pytest

from scanner.monitor import codes


def test_swing_codes_core_math():
    c = codes.swing(entry=100.0, stop=98.0, target=104.0, price=101.0,
                    highs=[101.5, 102.0], lows=[99.0, 100.5],
                    atr30=0.5, atr_daily=2.0, bars_held=10, max_bars=60, sessions_to_flatten=3)
    assert c["R"] == pytest.approx(0.5)                 # (101-100)/2
    assert c["P"] == pytest.approx(0.5)                # (101-98)/(104-98)
    assert c["MF"] == pytest.approx(1.0)                # best high 102 -> +1R
    assert c["MA"] == pytest.approx(-0.5)               # worst low 99 -> -0.5R
    assert c["C"] == pytest.approx(6.0)                 # (101-98)/0.5
    assert c["G"] == pytest.approx(1.5)                 # (101-98)/2.0
    assert c["B"] == "10/60" and c["F"] == 3


def test_fair_probability_is_clipped_outside_the_bracket():
    below = codes.swing(entry=100, stop=98, target=104, price=97, highs=[], lows=[],
                        atr30=1, atr_daily=1, bars_held=1, max_bars=60, sessions_to_flatten=1)
    assert below["P"] == 0.0 and below["C"] < 0


def test_render_is_one_compact_line_of_letters_and_numbers():
    line = codes.render({"R": 0.4213, "P": 0.47, "MF": 0.8, "MA": -0.3, "C": 2.06, "G": 0.81,
                         "B": "18/60", "F": 2})
    assert line == "R+0.42 P0.47 MF+0.80 MA-0.30 C2.1 G0.8 B18/60 F2"


def test_momentum_codes():
    m = codes.momentum(rank=3, universe=900, weight_pct=11.2, sector_share_pct=80.0,
                       trend_gap_pct=7.1, dollar_volume=2.5e9, mom_12_1=422.3)
    assert codes.render(m) == "K99.8 W11.2 S80 T+7.1 L2500 M+422"


def test_heat_is_total_risk_over_equity():
    assert codes.heat([(101.0, 98.0, 100), (50.0, 49.0, 200)], equity=100_000) == pytest.approx(0.5)
