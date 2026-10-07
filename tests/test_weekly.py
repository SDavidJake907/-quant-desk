"""Weekly review helpers: labels describe, numbers add up."""

from __future__ import annotations

import pytest

from scanner.ops import weekly as w


def test_trend_labels():
    rising = [100 + i for i in range(60)]
    falling = [200 - i for i in range(60)]
    assert w.trend_label(rising) == w.UP
    assert w.trend_label(falling) == w.DOWN
    # above its 50-day average but down over the month: mixed
    bump = [100.0] * 30 + [150.0] * 8 + [140.0] * 22
    assert w.trend_label(bump) == w.MIXED
    assert w.trend_label([1.0] * 10) == w.MIXED          # too little history


def test_week_change_and_stop_room():
    assert w.week_change([100, 101, 102, 103, 104, 110]) == pytest.approx(10.0)
    assert w.week_change([100, 110]) == 0.0
    assert w.stop_room(110.0, 100.0) == pytest.approx(9.0909, rel=1e-3)
    assert w.stop_room(110.0, None) is None
    assert w.pct(1, 0) == 0.0
