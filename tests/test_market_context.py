"""Market context lines for Gemma: plain, bounded, honest about missing data."""

from __future__ import annotations

from scanner.advisor import market_context as mc


def test_vix_mood_by_percentile():
    calm = [30.0] * 200 + [12.0]
    assert "calm" in mc.vix_text(calm)
    hot = [12.0] * 200 + [40.0]
    assert "elevated" in mc.vix_text(hot) and "VIX 40.0" in mc.vix_text(hot)
    assert mc.vix_text([]) == "VIX unavailable"


def test_breadth_share_above_average():
    up = [float(i) for i in range(60)]
    down = [float(60 - i) for i in range(60)]
    assert abs(mc.breadth([up, up, down]) - 200 / 3) < 1e-9
    assert mc.breadth([[1.0] * 10]) is None


def test_yield_and_events_and_decisions():
    assert "4.25%" in mc.yield_text([4.0] * 21 + [4.25]) and "+0.25 pts" in mc.yield_text([4.0] * 21 + [4.25])
    assert mc.yield_text([4.0]) == "10-year Treasury yield unavailable"
    line = mc.events_text({"Thu 10/08": ["FOMC Meeting Minutes"], "Mon 10/12": []})
    assert "Thu 10/08 FOMC Meeting Minutes" in line and "Mon" not in line
    assert "none" in mc.events_text({})
    d = mc.decisions_text([{"day": "2026-10-07", "buys": ["COHR", "FIX"], "sells": []},
                           {"day": "2026-10-08", "buys": [], "sells": []}])
    assert "2026-10-07: bought COHR, FIX" in d and "2026-10-08: held" in d
    assert mc.decisions_text([]) == "Your recent decisions: none yet."
