"""The last daily candles in words (advisor/candle_story.py, Gemma's labels 2026-10-25)."""

from datetime import date, timedelta

from scanner.advisor import candle_story as cs
from scanner.features.bars import Bars


def bars(rows):
    """rows: (open, high, low, close, volume), oldest first, from Monday 2026-10-05 on weekdays."""
    days, d = [], date(2026, 10, 5)
    while len(days) < len(rows):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    o, h, l, c, v = zip(*rows)
    return Bars(symbol="X", dates=tuple(days), opens=o, highs=h, lows=l, closes=c, volumes=v)


FLAT = [(100, 101, 99, 100, 1000)] * 20


def test_colour_gap_big_volume_and_range():
    b = bars(FLAT + [(105, 112, 104, 111, 3000)])           # gap up 5%, body 6/111 = 5.4%, vol 3x
    line = cs.candle(len(b.closes) - 1, b, atr_pct=2.0)
    assert line.startswith("Mon GREEN +11.0% GAP UP +5.0% BIG")
    assert "vol 3.0x HIGH" in line and "higher high" in line


def test_red_inside_day_and_low_volume():
    b = bars(FLAT + [(100.5, 100.8, 99.5, 99.8, 500)])
    line = cs.candle(len(b.closes) - 1, b, atr_pct=2.0)
    assert " RED -0.2%" in line and "BIG" not in line and "GAP" not in line
    assert "vol 0.5x LOW" in line and "inside day" in line


def test_text_needs_history_and_lists_the_last_five():
    assert cs.text(bars(FLAT[:5]), 2.0) == ""
    out = cs.text(bars(FLAT), 2.0)
    assert out.startswith("Last 5 days: ") and out.count(" | ") == 4
